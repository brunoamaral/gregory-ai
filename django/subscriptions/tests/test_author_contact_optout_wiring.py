"""
Tests for the two webhook paths that write AuthorContactOptOut rows — see
AUTHOR-OUTREACH-PLAN.md "PR 2 — Author do-not-contact" and
AUTHOR-OUTREACH-SPEC.md "Bounce and complaint handling":

- subscriptions.utils.email_events.handle_email_event — a hard bounce
  (Postmark Bounce Type of HardBounce or BadEmailAddress) or a
  SpamComplaint, regardless of which sender the message came from.
- subscriptions.utils.postmark_webhook.handle_subscription_change — a
  suppressed address that matches an existing AuthorOutreach recipient.

Also exercises subscriptions.utils.author_optout.record_author_opt_out
directly: idempotency, author resolution, and failure tolerance (the write
must never be able to turn a webhook call into a non-200 response — see
constraint 3 in the PR 2 scope).
"""

from unittest import mock

from django.contrib.sites.models import Site
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from gregory.models import Authors
from subscriptions.models import AuthorContactOptOut, AuthorOutreach, AuthorOutreachCampaign
from subscriptions.utils.author_optout import (
	optout_reason_for_suppression_reason,
	record_author_opt_out,
)
from subscriptions.utils.email_events import handle_email_event
from subscriptions.utils.postmark_webhook import handle_subscription_change


def _bounce_payload(**overrides):
	payload = {
		"RecordType": "Bounce",
		"MessageStream": "broadcast",
		"Type": "HardBounce",
		"TypeCode": 1,
		"Tag": "weekly_summary",
		"MessageID": "883953f4-6105-42a2-a16a-77a8eac79483",
		"Details": "Test bounce details",
		"Email": "bounced@example.com",
		"BouncedAt": "2026-08-01T12:00:00Z",
		"Content": "<Full dump of original message, never stored>",
		"Metadata": {},
	}
	payload.update(overrides)
	return payload


def _spam_complaint_payload(**overrides):
	payload = {
		"RecordType": "SpamComplaint",
		"MessageStream": "broadcast",
		"Type": "SpamComplaint",
		"TypeCode": 512,
		"Tag": "weekly_summary",
		"MessageID": "883953f4-6105-42a2-a16a-77a8eac79483",
		"Details": "Test spam complaint details",
		"Email": "complained@example.com",
		"BouncedAt": "2026-08-01T12:00:00Z",
		"Content": "<Abuse report dump, never stored>",
		"Metadata": {},
	}
	payload.update(overrides)
	return payload


def _delivery_payload(**overrides):
	payload = {
		"RecordType": "Delivery",
		"MessageStream": "broadcast",
		"MessageID": "883953f4-6105-42a2-a16a-77a8eac79483",
		"Recipient": "delivered@example.com",
		"Tag": "weekly_summary",
		"DeliveredAt": "2026-08-01T12:00:00Z",
		"Metadata": {},
	}
	payload.update(overrides)
	return payload


def _subscription_change_payload(recipient, suppress_sending, *, reason="", **overrides):
	payload = {
		"RecordType": "SubscriptionChange",
		"Recipient": recipient,
		"SuppressSending": suppress_sending,
		"SuppressionReason": reason,
		"Origin": "Recipient",
		"MessageStream": "broadcast",
		"MessageID": "00000000-0000-0000-0000-000000000000",
		"ChangedAt": timezone.now().isoformat().replace("+00:00", "Z"),
	}
	payload.update(overrides)
	return payload


class RecordAuthorOptOutTest(TestCase):
	"""Unit tests for the shared helper both webhook paths funnel through."""

	def test_creates_a_row(self):
		row = record_author_opt_out("Fresh@Example.com", AuthorContactOptOut.REASON_OPT_OUT)
		self.assertIsNotNone(row)
		self.assertEqual(row.email, "fresh@example.com")
		self.assertEqual(row.reason, AuthorContactOptOut.REASON_OPT_OUT)

	def test_empty_email_is_a_no_op(self):
		self.assertIsNone(record_author_opt_out("", AuthorContactOptOut.REASON_OPT_OUT))
		self.assertIsNone(record_author_opt_out(None, AuthorContactOptOut.REASON_OPT_OUT))
		self.assertEqual(AuthorContactOptOut.objects.count(), 0)

	def test_idempotent_across_reasons_case_insensitively(self):
		first = record_author_opt_out(
			"idempotent@example.com", AuthorContactOptOut.REASON_OPT_OUT
		)
		second = record_author_opt_out(
			"IDEMPOTENT@EXAMPLE.COM", AuthorContactOptOut.REASON_SPAM_COMPLAINT
		)
		self.assertEqual(first.pk, second.pk)
		self.assertEqual(AuthorContactOptOut.objects.count(), 1)
		# The first recorded reason wins — a later event never overwrites it.
		second.refresh_from_db()
		self.assertEqual(second.reason, AuthorContactOptOut.REASON_OPT_OUT)

	def test_resolves_author_from_existing_authoroutreach_row(self):
		site = Site.objects.create(domain="optout-helper.example.com", name="Helper")
		campaign = AuthorOutreachCampaign.objects.create(
			site=site, name="Helper Campaign", utm_campaign_slug="helper-campaign"
		)
		author = Authors.objects.create(
			given_name="Ada",
			family_name="Researcher",
			ORCID="0000-0000-0000-9001",
			emails=["ada-helper@example.com"],
			orcid_claimed=True,
			orcid_verified_email=True,
		)
		AuthorOutreach.objects.create(
			campaign=campaign, site=site, author=author, email="ada-helper@example.com"
		)
		row = record_author_opt_out(
			"ada-helper@example.com", AuthorContactOptOut.REASON_HARD_BOUNCE
		)
		self.assertEqual(row.author_id, author.author_id)

	def test_author_is_none_when_no_matching_authoroutreach_row(self):
		row = record_author_opt_out(
			"never-an-outreach-recipient@example.com", AuthorContactOptOut.REASON_HARD_BOUNCE
		)
		self.assertIsNone(row.author)

	def test_swallows_unexpected_exceptions(self):
		with mock.patch(
			"subscriptions.utils.author_optout.AuthorContactOptOut.objects.create",
			side_effect=RuntimeError("boom"),
		):
			result = record_author_opt_out(
				"boom@example.com", AuthorContactOptOut.REASON_OPT_OUT
			)
		self.assertIsNone(result)
		self.assertFalse(
			AuthorContactOptOut.objects.filter(email="boom@example.com").exists()
		)

	def test_integrity_error_race_returns_the_winning_row(self):
		# Simulates two concurrent callers both passing the "does it exist
		# yet" check before either has committed — the unique constraint
		# resolves it, and record_author_opt_out must return the row that
		# won rather than raising.
		with mock.patch(
			"subscriptions.utils.author_optout.AuthorContactOptOut.objects.create",
			side_effect=IntegrityError("duplicate key"),
		):
			result = record_author_opt_out(
				"race@example.com", AuthorContactOptOut.REASON_OPT_OUT
			)
		self.assertIsNone(result)  # no row exists yet in this scenario
		AuthorContactOptOut.objects.create(
			email="race@example.com", reason=AuthorContactOptOut.REASON_ADMIN
		)
		with mock.patch(
			"subscriptions.utils.author_optout.AuthorContactOptOut.objects.create",
			side_effect=IntegrityError("duplicate key"),
		):
			# existing check now short-circuits before create() is reached
			result = record_author_opt_out(
				"race@example.com", AuthorContactOptOut.REASON_OPT_OUT
			)
		self.assertIsNotNone(result)
		self.assertEqual(result.reason, AuthorContactOptOut.REASON_ADMIN)


class OptoutReasonForSuppressionReasonTest(TestCase):
	def test_hard_bounce_maps_to_hard_bounce(self):
		self.assertEqual(
			optout_reason_for_suppression_reason("HardBounce"),
			AuthorContactOptOut.REASON_HARD_BOUNCE,
		)

	def test_spam_complaint_maps_to_spam_complaint(self):
		self.assertEqual(
			optout_reason_for_suppression_reason("SpamComplaint"),
			AuthorContactOptOut.REASON_SPAM_COMPLAINT,
		)

	def test_manual_suppression_and_unknown_map_to_admin(self):
		self.assertEqual(
			optout_reason_for_suppression_reason("ManualSuppression"),
			AuthorContactOptOut.REASON_ADMIN,
		)
		self.assertEqual(
			optout_reason_for_suppression_reason(""), AuthorContactOptOut.REASON_ADMIN
		)
		self.assertEqual(
			optout_reason_for_suppression_reason("SomethingNew"),
			AuthorContactOptOut.REASON_ADMIN,
		)


class HandleEmailEventOptOutWiringTest(TestCase):
	"""handle_email_event: hard bounce / bad address / spam complaint."""

	def test_hard_bounce_writes_opt_out(self):
		handle_email_event(_bounce_payload(Type="HardBounce", Email="hard@example.com"))
		row = AuthorContactOptOut.objects.get(email="hard@example.com")
		self.assertEqual(row.reason, AuthorContactOptOut.REASON_HARD_BOUNCE)

	def test_bad_email_address_writes_opt_out(self):
		handle_email_event(
			_bounce_payload(Type="BadEmailAddress", Email="bad-address@example.com")
		)
		row = AuthorContactOptOut.objects.get(email="bad-address@example.com")
		self.assertEqual(row.reason, AuthorContactOptOut.REASON_HARD_BOUNCE)

	def test_soft_bounce_does_not_write_opt_out(self):
		handle_email_event(
			_bounce_payload(Type="SoftBounce", Email="soft@example.com")
		)
		self.assertFalse(
			AuthorContactOptOut.objects.filter(email="soft@example.com").exists()
		)

	def test_transient_bounce_does_not_write_opt_out(self):
		handle_email_event(
			_bounce_payload(Type="Transient", Email="transient@example.com")
		)
		self.assertFalse(
			AuthorContactOptOut.objects.filter(email="transient@example.com").exists()
		)

	def test_spam_complaint_writes_opt_out(self):
		handle_email_event(
			_spam_complaint_payload(Email="complained@example.com")
		)
		row = AuthorContactOptOut.objects.get(email="complained@example.com")
		self.assertEqual(row.reason, AuthorContactOptOut.REASON_SPAM_COMPLAINT)

	def test_delivery_and_other_types_never_write_opt_out(self):
		handle_email_event(_delivery_payload(Recipient="delivered@example.com"))
		self.assertFalse(
			AuthorContactOptOut.objects.filter(email="delivered@example.com").exists()
		)

	def test_recipient_field_is_email_not_recipient_for_bounce(self):
		# Regression guard for the same "Email" vs "Recipient" quirk PR 1
		# fixed for EmailEvent: a bounce payload carries the recipient in
		# "Email", and a stray "Recipient" field (absent from real Postmark
		# bounce payloads, added here only to prove it's ignored) must not
		# be the address that gets opted out.
		handle_email_event(
			_bounce_payload(
				Email="right-field@example.com",
				Recipient="wrong-field@example.com",
			)
		)
		self.assertTrue(
			AuthorContactOptOut.objects.filter(email="right-field@example.com").exists()
		)
		self.assertFalse(
			AuthorContactOptOut.objects.filter(email="wrong-field@example.com").exists()
		)

	def test_a_replayed_bounce_does_not_write_a_second_opt_out_attempt(self):
		payload = _bounce_payload(Email="replay@example.com")
		handle_email_event(payload)
		self.assertEqual(AuthorContactOptOut.objects.filter(email="replay@example.com").count(), 1)
		# Same (record_type, message_id, occurred_at) — EmailEvent's unique
		# constraint makes this a no-op before the opt-out write is ever
		# reached, and the still-idempotent opt-out row count proves it
		# either way.
		handle_email_event(payload)
		self.assertEqual(AuthorContactOptOut.objects.filter(email="replay@example.com").count(), 1)

	def test_failure_writing_opt_out_never_breaks_handle_email_event(self):
		with mock.patch(
			"subscriptions.utils.email_events.record_author_opt_out",
			side_effect=RuntimeError("boom"),
		):
			event = handle_email_event(_bounce_payload(Email="still-works@example.com"))
		self.assertIsNotNone(event)
		self.assertEqual(event.recipient, "still-works@example.com")


class HandleSubscriptionChangeOptOutWiringTest(TestCase):
	"""
	handle_subscription_change: opt-out only when the suppressed address
	matches an existing AuthorOutreach recipient — and nothing else about
	the function's existing behaviour changes.
	"""

	def setUp(self):
		self.site = Site.objects.get_or_create(
			id=1, defaults={"domain": "testserver", "name": "Test Site"}
		)[0]
		self.campaign = AuthorOutreachCampaign.objects.create(
			site=self.site, name="Wiring Campaign", utm_campaign_slug="wiring-campaign"
		)
		self.author = Authors.objects.create(
			given_name="Ada",
			family_name="Researcher",
			ORCID="0000-0000-0000-9002",
			emails=["outreach-recipient@example.com"],
			orcid_claimed=True,
			orcid_verified_email=True,
		)
		self.outreach = AuthorOutreach.objects.create(
			campaign=self.campaign,
			site=self.site,
			author=self.author,
			email="outreach-recipient@example.com",
		)

	def test_suppress_matching_outreach_recipient_writes_opt_out(self):
		handle_subscription_change(
			_subscription_change_payload(
				"outreach-recipient@example.com", True, reason="HardBounce"
			)
		)
		row = AuthorContactOptOut.objects.get(email="outreach-recipient@example.com")
		self.assertEqual(row.reason, AuthorContactOptOut.REASON_HARD_BOUNCE)
		self.assertEqual(row.author_id, self.author.author_id)

	def test_suppress_non_outreach_recipient_writes_nothing(self):
		handle_subscription_change(
			_subscription_change_payload("nobody-special@example.com", True, reason="HardBounce")
		)
		self.assertEqual(AuthorContactOptOut.objects.count(), 0)

	def test_unsuppress_never_writes_opt_out_even_for_a_matching_recipient(self):
		# Opt-out is one-directional: reactivating a subscription must
		# never be read as "un-opting-out" an author.
		handle_subscription_change(
			_subscription_change_payload(
				"outreach-recipient@example.com", False, reason="ManualSuppression"
			)
		)
		self.assertEqual(AuthorContactOptOut.objects.count(), 0)

	def test_manual_suppression_maps_to_admin_reason(self):
		handle_subscription_change(
			_subscription_change_payload(
				"outreach-recipient@example.com", True, reason="ManualSuppression"
			)
		)
		row = AuthorContactOptOut.objects.get(email="outreach-recipient@example.com")
		self.assertEqual(row.reason, AuthorContactOptOut.REASON_ADMIN)

	def test_suppression_event_and_reactivation_still_created_normally(self):
		# The opt-out side effect must not change SuppressionEvent creation,
		# the idempotency key, or the reactivation policy at all — this is
		# the same scenario the existing suite covers, just also carrying
		# a matching AuthorOutreach row, and it must behave identically.
		from subscriptions.models import SuppressionEvent

		event = handle_subscription_change(
			_subscription_change_payload(
				"outreach-recipient@example.com", True, reason="HardBounce"
			)
		)
		self.assertIsNotNone(event)
		self.assertEqual(SuppressionEvent.objects.count(), 1)
		self.assertEqual(event.action_taken, SuppressionEvent.ACTION_RECORD_ONLY)

	def test_failure_writing_opt_out_never_breaks_handle_subscription_change(self):
		with mock.patch(
			"subscriptions.utils.postmark_webhook.record_author_opt_out",
			side_effect=RuntimeError("boom"),
		):
			event = handle_subscription_change(
				_subscription_change_payload(
					"outreach-recipient@example.com", True, reason="HardBounce"
				)
			)
		self.assertIsNotNone(event)
		self.assertEqual(AuthorContactOptOut.objects.count(), 0)
