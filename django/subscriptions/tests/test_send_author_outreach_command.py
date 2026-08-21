"""
Tests for the send_author_outreach management command — see
docs/author-outreach-spec.md "Queue and approval", "Safety limits", "Addressing
and sending" and docs/author-outreach.md.

The Postmark call is mocked in every test (subscriptions.management.
commands.utils.send_email.requests.post) — no test in this file ever
performs a real network call, per this PR's own constraints.

Covers: approved-only sending (a pending row is never sent), --dry-run
writes nothing, a halted campaign sends nothing, a circuit breaker
tripping mid-run halts the campaign and stops before the tripping row is
sent, a Postmark 406 marks a row skipped, any other send failure marks a
row failed with no automatic retry, --test-to renders without mutating
the row, the daily send limit, the send-time opt-out recheck, and the
privacy guard that Metadata carries nothing person-resolvable.
"""

from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest import mock

from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from gregory.models import Articles, Authors, Team
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import (
	AuthorContactOptOut,
	AuthorOutreach,
	AuthorOutreachCampaign,
	EmailMessage,
)

SEND_EMAIL_POST_TARGET = "subscriptions.management.commands.utils.send_email.requests.post"
SLEEP_TARGET = "subscriptions.management.commands.send_author_outreach.time.sleep"


def _mock_response(status_code=200, error_code=0, message_id="msg-1", message="OK"):
	response = mock.Mock()
	response.status_code = status_code
	response.json.return_value = {
		"ErrorCode": error_code,
		"Message": message,
		"MessageID": message_id,
	}
	return response


class SendAuthorOutreachCommandTestCase(TestCase):
	def _new_world(self, tag, campaign_kwargs=None):
		org = Organization.objects.create(name=f"Org {tag}", slug=f"org-{tag}")
		Team.objects.create(name=f"Team {tag}", organization=org, slug=f"team-{tag}")
		site = Site.objects.create(domain=f"{tag}.example.com", name=tag)
		custom_settings = CustomSetting.objects.create(
			site=site,
			title=f"CS {tag}",
			has_author_pages=True,
			postmark_api_token="test-token",
			postmark_api_url="https://api.postmark.test/email",
		)
		defaults = dict(
			site=site,
			name=f"Campaign {tag}",
			utm_campaign_slug=f"campaign-{tag}",
			mode=AuthorOutreachCampaign.MODE_UPCOMING,
			enabled=True,
			send_rate_per_minute=6000,  # keep tests fast even without patching sleep
			daily_send_limit=50,
		)
		if campaign_kwargs:
			defaults.update(campaign_kwargs)
		campaign = AuthorOutreachCampaign.objects.create(**defaults)
		return SimpleNamespace(org=org, site=site, custom_settings=custom_settings, campaign=campaign)

	def _new_row(self, w, tag, status=AuthorOutreach.STATUS_APPROVED):
		author = Authors.objects.create(
			given_name="Jane",
			family_name=f"Researcher {tag}",
			ORCID=f"orcid-{tag}",
			emails=[f"jane.{tag}@example.org"],
			orcid_verified_email=True,
			orcid_claimed=True,
		)
		article = Articles.objects.create(
			title=f"Article {tag}",
			link=f"https://example.com/{tag}",
			doi=f"10.9999/{tag}",
			published_date=timezone.now() - timedelta(days=1),
		)
		article.authors.add(author)
		row = AuthorOutreach.objects.create(
			campaign=w.campaign,
			site=w.site,
			author=author,
			email=author.emails[0],
			status=status,
		)
		row.articles.set([article])
		return row

	def _run(self, campaign_slug, **extra):
		out = StringIO()
		call_command("send_author_outreach", campaign=campaign_slug, stdout=out, **extra)
		return out.getvalue()

	# ------------------------------------------------------------------
	# Approved-only / pending never sent
	# ------------------------------------------------------------------

	def test_pending_row_is_never_sent(self):
		w = self._new_world("pending1")
		row = self._new_row(w, "p1", status=AuthorOutreach.STATUS_PENDING)

		with mock.patch(SEND_EMAIL_POST_TARGET) as mock_post:
			self._run(w.campaign.utm_campaign_slug)

		mock_post.assert_not_called()
		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_PENDING)
		self.assertEqual(EmailMessage.objects.count(), 0)

	def test_approved_row_is_sent_and_marked_sent(self):
		w = self._new_world("approved1")
		row = self._new_row(w, "a1", status=AuthorOutreach.STATUS_APPROVED)

		with mock.patch(SEND_EMAIL_POST_TARGET) as mock_post:
			mock_post.return_value = _mock_response()
			self._run(w.campaign.utm_campaign_slug)

		mock_post.assert_called_once()
		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_SENT)
		self.assertIsNotNone(row.sent_at)
		self.assertIsNotNone(row.email_message)
		self.assertEqual(EmailMessage.objects.count(), 1)
		message = EmailMessage.objects.get()
		self.assertTrue(message.accepted)
		self.assertEqual(message.tag, "author_outreach")
		self.assertEqual(message.recipient, row.email)

	def test_mixed_statuses_only_approved_row_is_sent(self):
		w = self._new_world("mixed1")
		pending_row = self._new_row(w, "m1", status=AuthorOutreach.STATUS_PENDING)
		approved_row = self._new_row(w, "m2", status=AuthorOutreach.STATUS_APPROVED)
		sent_row = self._new_row(w, "m3", status=AuthorOutreach.STATUS_SENT)

		with mock.patch(SEND_EMAIL_POST_TARGET) as mock_post:
			mock_post.return_value = _mock_response()
			self._run(w.campaign.utm_campaign_slug)

		self.assertEqual(mock_post.call_count, 1)
		call_kwargs = mock_post.call_args.kwargs
		self.assertEqual(call_kwargs["json"]["To"], approved_row.email)
		pending_row.refresh_from_db()
		sent_row.refresh_from_db()
		self.assertEqual(pending_row.status, AuthorOutreach.STATUS_PENDING)
		self.assertEqual(sent_row.status, AuthorOutreach.STATUS_SENT)

	# ------------------------------------------------------------------
	# --dry-run
	# ------------------------------------------------------------------

	def test_dry_run_writes_no_email_message_and_sends_nothing(self):
		w = self._new_world("dryrun1")
		row = self._new_row(w, "d1", status=AuthorOutreach.STATUS_APPROVED)

		with mock.patch(SEND_EMAIL_POST_TARGET) as mock_post:
			output = self._run(w.campaign.utm_campaign_slug, dry_run=True)

		mock_post.assert_not_called()
		self.assertEqual(EmailMessage.objects.count(), 0)
		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_APPROVED)
		self.assertIsNone(row.sent_at)
		self.assertIn("DRY RUN", output)

	# ------------------------------------------------------------------
	# Halted campaign
	# ------------------------------------------------------------------

	def test_halted_campaign_sends_nothing(self):
		w = self._new_world(
			"halted1",
			campaign_kwargs={"halted": True, "halted_reason": "manually halted for test"},
		)
		row = self._new_row(w, "h1", status=AuthorOutreach.STATUS_APPROVED)

		with mock.patch(SEND_EMAIL_POST_TARGET) as mock_post:
			output = self._run(w.campaign.utm_campaign_slug)

		mock_post.assert_not_called()
		self.assertEqual(EmailMessage.objects.count(), 0)
		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_APPROVED)
		self.assertIn("halted", output.lower())

	# ------------------------------------------------------------------
	# Circuit breakers
	# ------------------------------------------------------------------

	def test_breaker_trips_before_send_and_halts_campaign(self):
		w = self._new_world("breaker1", campaign_kwargs={"complaint_halt_absolute": 1})
		# A prior send (already SENT) that later drew a complaint via the
		# webhook — this is what should trip the breaker for the NEW
		# approved row below, before that row is ever handed to Postmark.
		complained_author = Authors.objects.create(
			given_name="A",
			family_name="Complainer",
			ORCID="orcid-complainer",
			emails=["complainer@example.org"],
			orcid_verified_email=True,
			orcid_claimed=True,
		)
		complained_message = EmailMessage.objects.create(
			recipient="complainer@example.org",
			tag="author_outreach",
			site=w.site,
			accepted=True,
			complained_at=timezone.now(),
		)
		AuthorOutreach.objects.create(
			campaign=w.campaign,
			site=w.site,
			author=complained_author,
			email="complainer@example.org",
			status=AuthorOutreach.STATUS_SENT,
			email_message=complained_message,
		)

		row = self._new_row(w, "b1", status=AuthorOutreach.STATUS_APPROVED)

		with mock.patch(SEND_EMAIL_POST_TARGET) as mock_post:
			output = self._run(w.campaign.utm_campaign_slug)

		mock_post.assert_not_called()
		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_APPROVED)
		w.campaign.refresh_from_db()
		self.assertTrue(w.campaign.halted)
		self.assertIn("spam complaint", w.campaign.halted_reason)
		self.assertIn("halt", output.lower())

	def test_breaker_trip_stops_remaining_rows_in_same_run(self):
		w = self._new_world("breaker2", campaign_kwargs={"complaint_halt_absolute": 1})
		row1 = self._new_row(w, "r1", status=AuthorOutreach.STATUS_APPROVED)
		row2 = self._new_row(w, "r2", status=AuthorOutreach.STATUS_APPROVED)

		# First send succeeds normally...
		responses = [_mock_response()]

		def _side_effect(*args, **kwargs):
			return responses.pop(0) if responses else _mock_response()

		with mock.patch(SEND_EMAIL_POST_TARGET, side_effect=_side_effect):
			self._run(w.campaign.utm_campaign_slug, limit=1)

		row1.refresh_from_db()
		self.assertEqual(row1.status, AuthorOutreach.STATUS_SENT)

		# ...then a complaint webhook comes in for it...
		row1.email_message.complained_at = timezone.now()
		row1.email_message.save(update_fields=["complained_at"])

		# ...and the second run must refuse to send row2.
		with mock.patch(SEND_EMAIL_POST_TARGET) as mock_post:
			self._run(w.campaign.utm_campaign_slug)

		mock_post.assert_not_called()
		row2.refresh_from_db()
		self.assertEqual(row2.status, AuthorOutreach.STATUS_APPROVED)
		w.campaign.refresh_from_db()
		self.assertTrue(w.campaign.halted)

	# ------------------------------------------------------------------
	# Postmark 406 / send failure — no automatic retry
	# ------------------------------------------------------------------

	def test_postmark_406_marks_row_skipped(self):
		w = self._new_world("inactive1")
		row = self._new_row(w, "i1", status=AuthorOutreach.STATUS_APPROVED)

		with mock.patch(SEND_EMAIL_POST_TARGET) as mock_post:
			mock_post.return_value = _mock_response(status_code=422, error_code=406, message="Inactive")
			self._run(w.campaign.utm_campaign_slug)

		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_SKIPPED)
		self.assertIsNotNone(row.email_message)
		self.assertEqual(EmailMessage.objects.get().error_code, 406)

	def test_send_failure_marks_row_failed_and_slot_stays_burned(self):
		w = self._new_world("fail1")
		row = self._new_row(w, "f1", status=AuthorOutreach.STATUS_APPROVED)

		with mock.patch(SEND_EMAIL_POST_TARGET) as mock_post:
			mock_post.return_value = _mock_response(status_code=500, error_code=999, message="Server error")
			self._run(w.campaign.utm_campaign_slug)

		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_FAILED)

		# No automatic retry: a second run must not pick this row up again
		# because it is no longer status=approved.
		with mock.patch(SEND_EMAIL_POST_TARGET) as mock_post2:
			self._run(w.campaign.utm_campaign_slug)
		mock_post2.assert_not_called()
		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_FAILED)

	# ------------------------------------------------------------------
	# --test-to
	# ------------------------------------------------------------------

	def test_test_to_sends_without_changing_row_status(self):
		w = self._new_world("testto1")
		row = self._new_row(w, "t1", status=AuthorOutreach.STATUS_APPROVED)

		with mock.patch(SEND_EMAIL_POST_TARGET) as mock_post:
			mock_post.return_value = _mock_response()
			self._run(w.campaign.utm_campaign_slug, test_to="preview@example.com")

		mock_post.assert_called_once()
		call_kwargs = mock_post.call_args.kwargs
		self.assertEqual(call_kwargs["json"]["To"], "preview@example.com")
		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_APPROVED)
		self.assertIsNone(row.sent_at)
		self.assertIsNone(row.email_message)

	def test_test_to_works_even_on_a_pending_row(self):
		w = self._new_world("testto2")
		row = self._new_row(w, "t2", status=AuthorOutreach.STATUS_PENDING)

		with mock.patch(SEND_EMAIL_POST_TARGET) as mock_post:
			mock_post.return_value = _mock_response()
			self._run(w.campaign.utm_campaign_slug, test_to="preview2@example.com")

		mock_post.assert_called_once()
		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_PENDING)

	# ------------------------------------------------------------------
	# Daily send limit
	# ------------------------------------------------------------------

	def test_daily_send_limit_stops_further_sends(self):
		w = self._new_world("dailylimit1", campaign_kwargs={"daily_send_limit": 1})
		row1 = self._new_row(w, "dl1", status=AuthorOutreach.STATUS_APPROVED)
		row2 = self._new_row(w, "dl2", status=AuthorOutreach.STATUS_APPROVED)

		with mock.patch(SEND_EMAIL_POST_TARGET) as mock_post:
			mock_post.return_value = _mock_response()
			self._run(w.campaign.utm_campaign_slug)

		self.assertEqual(mock_post.call_count, 1)
		row1.refresh_from_db()
		row2.refresh_from_db()
		statuses = {row1.status, row2.status}
		self.assertIn(AuthorOutreach.STATUS_SENT, statuses)
		self.assertIn(AuthorOutreach.STATUS_APPROVED, statuses)

	# ------------------------------------------------------------------
	# Send-time opt-out recheck
	# ------------------------------------------------------------------

	def test_send_time_opt_out_recheck_skips_without_calling_postmark(self):
		w = self._new_world("recheck1")
		row = self._new_row(w, "rc1", status=AuthorOutreach.STATUS_APPROVED)
		# Simulate an opt-out recorded after the queue was built (e.g. via
		# a hard bounce on a different message) but before this run.
		AuthorContactOptOut.objects.create(email=row.email, reason=AuthorContactOptOut.REASON_OPT_OUT)

		with mock.patch(SEND_EMAIL_POST_TARGET) as mock_post:
			self._run(w.campaign.utm_campaign_slug)

		mock_post.assert_not_called()
		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_SKIPPED)

	# ------------------------------------------------------------------
	# Metadata privacy
	# ------------------------------------------------------------------

	def test_metadata_contains_no_person_resolvable_value(self):
		w = self._new_world("metaprivacy1")
		row = self._new_row(w, "mp1", status=AuthorOutreach.STATUS_APPROVED)

		with mock.patch(SEND_EMAIL_POST_TARGET) as mock_post:
			mock_post.return_value = _mock_response()
			self._run(w.campaign.utm_campaign_slug)

		payload = mock_post.call_args.kwargs["json"]
		metadata = payload["Metadata"]
		self.assertEqual(set(metadata.keys()), {"msg_token", "campaign"})
		self.assertEqual(metadata["campaign"], w.campaign.utm_campaign_slug)
		self.assertNotEqual(metadata["msg_token"], "")
		# Nothing resolvable to the author: no pk, no ORCID, no email
		# anywhere in Metadata's values.
		serialized = str(metadata)
		self.assertNotIn(row.email, serialized)
		self.assertNotIn(row.author.ORCID, serialized)
		self.assertNotIn(str(row.author.pk), metadata.values())

	def test_reply_to_and_tracking_flags_set_for_outreach_only(self):
		w = self._new_world("tracking1", campaign_kwargs={"reply_to": "bruno@brain-regeneration.com"})
		self._new_row(w, "tr1", status=AuthorOutreach.STATUS_APPROVED)

		with mock.patch(SEND_EMAIL_POST_TARGET) as mock_post:
			mock_post.return_value = _mock_response()
			self._run(w.campaign.utm_campaign_slug)

		payload = mock_post.call_args.kwargs["json"]
		self.assertEqual(payload["ReplyTo"], "bruno@brain-regeneration.com")
		self.assertEqual(payload["TrackOpens"], True)
		# HtmlOnly, deliberately: the opt-out link carries data-pm-no-track,
		# which is HTML-only, so tracking the text body would rewrite and
		# track "never contact me again" regardless of the HTML marker.
		self.assertEqual(payload["TrackLinks"], "HtmlOnly")
		self.assertEqual(payload["Tag"], "author_outreach")
		self.assertEqual(payload["MessageStream"], "broadcast")

	# ------------------------------------------------------------------
	# Retrospective campaign guard
	# ------------------------------------------------------------------

	def test_retrospective_campaign_without_body_template_refuses_up_front(self):
		w = self._new_world(
			"retroguard1",
			campaign_kwargs={"mode": AuthorOutreachCampaign.MODE_RETROSPECTIVE, "featured_within_days": 90},
		)
		row = self._new_row(w, "rg1", status=AuthorOutreach.STATUS_APPROVED)

		with mock.patch(SEND_EMAIL_POST_TARGET) as mock_post:
			from django.core.management import CommandError

			with self.assertRaises(CommandError):
				self._run(w.campaign.utm_campaign_slug)

		mock_post.assert_not_called()
		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_APPROVED)
