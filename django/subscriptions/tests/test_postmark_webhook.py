"""
Tests for the Postmark webhook (suppression / reactivation):

- subscriptions.utils.postmark_webhook.handle_subscription_change — parsing,
  idempotency, ordering, the reactivation policy, and the staleness cap.
- subscriptions.views.postmark_webhook — auth, RecordType dispatch, and the
  fast-200 contract Postmark's retry schedule depends on.

See docs/subscriptions.md#suppression-and-reactivation-webhook for the policy this
implements, and PLAN.md's "Confirmed configuration" / "Reactivation policy —
decided" sections for why each rule exists.
"""

import base64
import json
import os
from datetime import timedelta
from unittest import mock

from django.contrib.sites.models import Site
from django.test import Client, TestCase
from django.utils import timezone

from gregory.models import Team
from organizations.models import Organization
from subscriptions.models import (
	ListSubscription,
	Lists,
	SuppressionEvent,
	Subscribers,
)
from subscriptions.utils.postmark_webhook import (
	REACTIVATION_MAX_AGE,
	handle_subscription_change,
)

WEBHOOK_ENV = {
	"POSTMARK_WEBHOOK_USERNAME": "webhook-user",
	"POSTMARK_WEBHOOK_PASSWORD": "webhook-pass",
}


def _basic_auth_header(username, password):
	token = base64.b64encode(f"{username}:{password}".encode()).decode()
	return f"Basic {token}"


def _iso(dt):
	return dt.isoformat().replace("+00:00", "Z")


def _subscription_change_payload(
	recipient,
	suppress_sending,
	*,
	reason="",
	origin="Recipient",
	changed_at=None,
	message_id="00000000-0000-0000-0000-000000000000",
	message_stream="broadcast",
):
	return {
		"RecordType": "SubscriptionChange",
		"Recipient": recipient,
		"SuppressSending": suppress_sending,
		"SuppressionReason": reason,
		"Origin": origin,
		"MessageStream": message_stream,
		"MessageID": message_id,
		"ChangedAt": _iso(changed_at or timezone.now()),
	}


class SuppressionWebhookProcessingTestCase(TestCase):
	"""Base fixtures shared by the handle_subscription_change tests."""

	def setUp(self):
		self.org = Organization.objects.create(name="Webhook Org", slug="webhook-org")
		self.team = Team.objects.create(
			name="Webhook Team", organization=self.org, slug="webhook-team"
		)
		self.site = Site.objects.get_or_create(
			id=1, defaults={"domain": "testserver", "name": "Test Site"}
		)[0]
		self.list_a = Lists.objects.create(list_name="List A", team=self.team)
		self.list_b = Lists.objects.create(list_name="List B", team=self.team)

	def _make_subscriber(self, email, lists=()):
		subscriber = Subscribers.objects.create(
			first_name="Web", last_name="Hook", email=email, active=True
		)
		for lst in lists:
			ListSubscription.objects.create(
				subscriber=subscriber, list=lst, is_active=True
			)
		return subscriber


class SuppressionEventCreationTest(SuppressionWebhookProcessingTestCase):
	def test_suppress_deactivates_subscriber_and_records_affected_lists(self):
		subscriber = self._make_subscriber(
			"bounced@example.com", lists=[self.list_a, self.list_b]
		)
		sub_a = ListSubscription.objects.get(subscriber=subscriber, list=self.list_a)
		sub_b = ListSubscription.objects.get(subscriber=subscriber, list=self.list_b)

		payload = _subscription_change_payload(
			"bounced@example.com", True, reason="HardBounce", origin="Recipient"
		)
		event = handle_subscription_change(payload)

		subscriber.refresh_from_db()
		self.assertFalse(subscriber.active)
		self.assertIsNotNone(event)
		self.assertEqual(event.action_taken, SuppressionEvent.ACTION_SUPPRESSED)
		self.assertEqual(
			sorted(event.deactivated_list_subscription_ids),
			sorted([sub_a.pk, sub_b.pk]),
		)

	def test_unknown_recipient_is_recorded_without_creating_a_subscriber(self):
		payload = _subscription_change_payload(
			"never-seen@example.com", True, reason="HardBounce"
		)
		event = handle_subscription_change(payload)

		self.assertIsNotNone(event)
		self.assertIsNone(event.subscriber)
		self.assertEqual(event.action_taken, SuppressionEvent.ACTION_RECORD_ONLY)
		self.assertFalse(
			Subscribers.objects.filter(email="never-seen@example.com").exists()
		)

	def test_message_stream_other_than_broadcast_is_recorded_not_acted_on(self):
		subscriber = self._make_subscriber("stream@example.com", lists=[self.list_a])

		payload = _subscription_change_payload(
			"stream@example.com",
			True,
			reason="HardBounce",
			message_stream="some-other-stream",
		)
		event = handle_subscription_change(payload)

		subscriber.refresh_from_db()
		self.assertTrue(subscriber.active)
		self.assertIsNotNone(event)
		self.assertEqual(event.action_taken, SuppressionEvent.ACTION_RECORD_ONLY)
		self.assertIn("some-other-stream", event.flag_reason)


class ReplayAndOrderingTest(SuppressionWebhookProcessingTestCase):
	def test_replayed_event_is_a_no_op(self):
		subscriber = self._make_subscriber("replay@example.com", lists=[self.list_a])
		changed_at = timezone.now()

		payload = _subscription_change_payload(
			"replay@example.com", True, reason="HardBounce", changed_at=changed_at
		)
		first = handle_subscription_change(payload)
		self.assertIsNotNone(first)

		second = handle_subscription_change(dict(payload))
		self.assertIsNone(second)

		subscriber.refresh_from_db()
		self.assertEqual(
			SuppressionEvent.objects.filter(email="replay@example.com").count(), 1
		)

	def test_two_distinct_suppressions_with_all_zero_message_id_are_both_recorded(self):
		self._make_subscriber("zero-one@example.com", lists=[self.list_a])
		self._make_subscriber("zero-two@example.com", lists=[self.list_a])

		payload_one = _subscription_change_payload(
			"zero-one@example.com", True, reason="HardBounce"
		)
		payload_two = _subscription_change_payload(
			"zero-two@example.com", True, reason="HardBounce"
		)

		event_one = handle_subscription_change(payload_one)
		event_two = handle_subscription_change(payload_two)

		self.assertIsNotNone(event_one)
		self.assertIsNotNone(event_two)
		self.assertEqual(event_one.message_id, "00000000-0000-0000-0000-000000000000")
		self.assertEqual(event_two.message_id, "00000000-0000-0000-0000-000000000000")
		self.assertNotEqual(event_one.pk, event_two.pk)

	def test_out_of_order_event_older_than_latest_applied_is_ignored(self):
		subscriber = self._make_subscriber("order@example.com", lists=[self.list_a])
		now = timezone.now()

		newer_payload = _subscription_change_payload(
			"order@example.com", True, reason="HardBounce", changed_at=now
		)
		handle_subscription_change(newer_payload)

		older_payload = _subscription_change_payload(
			"order@example.com",
			False,
			reason="HardBounce",
			changed_at=now - timedelta(hours=1),
		)
		result = handle_subscription_change(older_payload)

		self.assertIsNone(result)
		subscriber.refresh_from_db()
		# The newer suppression must still stand -- the stale unsuppress did
		# not undo it.
		self.assertFalse(subscriber.active)


class ReactivationPolicyTest(SuppressionWebhookProcessingTestCase):
	def _suppress(self, email, lists, *, reason, changed_at=None):
		subscriber = self._make_subscriber(email, lists=lists)
		payload = _subscription_change_payload(
			email, True, reason=reason, origin="Recipient", changed_at=changed_at
		)
		event = handle_subscription_change(payload)
		return subscriber, event

	def test_hard_bounce_and_manual_suppression_auto_restore(self):
		for reason in ("HardBounce", "ManualSuppression"):
			email = f"{reason.lower()}@example.com"
			subscriber, suppress_event = self._suppress(
				email, [self.list_a, self.list_b], reason=reason
			)
			sub_a = ListSubscription.objects.get(
				subscriber=subscriber, list=self.list_a
			)
			sub_b = ListSubscription.objects.get(
				subscriber=subscriber, list=self.list_b
			)
			self.assertFalse(sub_a.is_active)
			self.assertFalse(sub_b.is_active)

			unsuppress_payload = _subscription_change_payload(
				email,
				False,
				reason=reason,
				changed_at=suppress_event.changed_at + timedelta(days=1),
			)
			event = handle_subscription_change(unsuppress_payload)

			subscriber.refresh_from_db()
			sub_a.refresh_from_db()
			sub_b.refresh_from_db()
			self.assertEqual(event.action_taken, SuppressionEvent.ACTION_AUTO_RESTORED)
			self.assertTrue(subscriber.active)
			self.assertTrue(sub_a.is_active)
			self.assertTrue(sub_b.is_active)
			self.assertIsNone(sub_a.unsubscribed_at)
			self.assertIsNone(sub_b.unsubscribed_at)

	def test_spam_complaint_never_auto_restores_even_from_customer_origin(self):
		subscriber, suppress_event = self._suppress(
			"spam@example.com", [self.list_a], reason="SpamComplaint"
		)

		unsuppress_payload = _subscription_change_payload(
			"spam@example.com",
			False,
			reason="SpamComplaint",
			origin="Customer",
			changed_at=suppress_event.changed_at + timedelta(days=1),
		)
		event = handle_subscription_change(unsuppress_payload)

		subscriber.refresh_from_db()
		self.assertEqual(event.action_taken, SuppressionEvent.ACTION_RECORD_ONLY)
		self.assertIn("sticky", event.flag_reason.lower())
		self.assertFalse(subscriber.active)

	def test_missing_or_unrecognised_reason_on_unsuppress_is_flagged_not_restored(self):
		subscriber, suppress_event = self._suppress(
			"unclear@example.com", [self.list_a], reason="HardBounce"
		)

		for offset, bad_reason in enumerate(("", "SomethingNew"), start=1):
			unsuppress_payload = _subscription_change_payload(
				"unclear@example.com",
				False,
				reason=bad_reason,
				changed_at=suppress_event.changed_at + timedelta(days=offset),
			)
			event = handle_subscription_change(unsuppress_payload)
			self.assertEqual(event.action_taken, SuppressionEvent.ACTION_RECORD_ONLY)

		subscriber.refresh_from_db()
		self.assertFalse(subscriber.active)

	def test_stale_hard_bounce_unsuppress_is_flagged_not_restored(self):
		self.assertEqual(REACTIVATION_MAX_AGE, timedelta(days=365))
		suppress_time = timezone.now() - timedelta(days=13 * 30)
		subscriber, suppress_event = self._suppress(
			"stale@example.com",
			[self.list_a],
			reason="HardBounce",
			changed_at=suppress_time,
		)

		unsuppress_payload = _subscription_change_payload(
			"stale@example.com",
			False,
			reason="HardBounce",
			changed_at=timezone.now(),
		)
		event = handle_subscription_change(unsuppress_payload)

		subscriber.refresh_from_db()
		self.assertEqual(event.action_taken, SuppressionEvent.ACTION_RECORD_ONLY)
		self.assertIn("365", event.flag_reason)
		self.assertFalse(subscriber.active)

	def test_unsuppress_with_no_matching_suppression_event_is_flagged_not_restored(
		self,
	):
		subscriber = self._make_subscriber("orphan@example.com", lists=[self.list_a])
		subscriber.active = False
		subscriber.save(update_fields=["active"])

		unsuppress_payload = _subscription_change_payload(
			"orphan@example.com", False, reason="HardBounce"
		)
		event = handle_subscription_change(unsuppress_payload)

		subscriber.refresh_from_db()
		self.assertEqual(event.action_taken, SuppressionEvent.ACTION_RECORD_ONLY)
		self.assertIn("No matching SuppressionEvent", event.flag_reason)
		self.assertFalse(subscriber.active)

	def test_reactivation_restores_exactly_the_deactivated_set(self):
		"""
		Regression test for the reactivation blocker: a subscriber who had
		already left List B on their own, before List A got them suppressed,
		must NOT be re-subscribed to List B by the later unsuppress.
		"""
		subscriber = self._make_subscriber(
			"exact-set@example.com", lists=[self.list_a, self.list_b]
		)
		sub_b = ListSubscription.objects.get(subscriber=subscriber, list=self.list_b)
		sub_b.is_active = False
		sub_b.unsubscribed_at = timezone.now() - timedelta(days=30)
		sub_b.save(update_fields=["is_active", "unsubscribed_at"])

		suppress_payload = _subscription_change_payload(
			"exact-set@example.com", True, reason="HardBounce"
		)
		suppress_event = handle_subscription_change(suppress_payload)
		sub_a = ListSubscription.objects.get(subscriber=subscriber, list=self.list_a)

		# Only List A was active at suppression time, so only List A should
		# be in the recorded set.
		self.assertEqual(suppress_event.deactivated_list_subscription_ids, [sub_a.pk])

		unsuppress_payload = _subscription_change_payload(
			"exact-set@example.com",
			False,
			reason="HardBounce",
			changed_at=suppress_event.changed_at + timedelta(days=1),
		)
		handle_subscription_change(unsuppress_payload)

		subscriber.refresh_from_db()
		sub_a.refresh_from_db()
		sub_b.refresh_from_db()
		self.assertTrue(subscriber.active)
		self.assertTrue(sub_a.is_active)
		# List B stays exactly as the subscriber left it.
		self.assertFalse(sub_b.is_active)
		self.assertIsNotNone(sub_b.unsubscribed_at)


class PostmarkWebhookViewTest(SuppressionWebhookProcessingTestCase):
	def setUp(self):
		super().setUp()
		self.client = Client()
		self.url = "/webhooks/"

	def _post(self, payload, *, authorized=True):
		headers = {}
		if authorized:
			headers["HTTP_AUTHORIZATION"] = _basic_auth_header(
				WEBHOOK_ENV["POSTMARK_WEBHOOK_USERNAME"],
				WEBHOOK_ENV["POSTMARK_WEBHOOK_PASSWORD"],
			)
		return self.client.post(
			self.url,
			data=json.dumps(payload),
			content_type="application/json",
			**headers,
		)

	@mock.patch.dict(os.environ, WEBHOOK_ENV)
	def test_unauthenticated_post_is_rejected_with_403(self):
		payload = _subscription_change_payload(
			"nope@example.com", True, reason="HardBounce"
		)
		response = self._post(payload, authorized=False)
		self.assertEqual(response.status_code, 403)

	@mock.patch.dict(os.environ, WEBHOOK_ENV)
	def test_wrong_credentials_are_rejected_with_403(self):
		payload = _subscription_change_payload(
			"nope@example.com", True, reason="HardBounce"
		)
		response = self.client.post(
			self.url,
			data=json.dumps(payload),
			content_type="application/json",
			HTTP_AUTHORIZATION=_basic_auth_header("wrong", "creds"),
		)
		self.assertEqual(response.status_code, 403)

	@mock.patch.dict(os.environ, WEBHOOK_ENV)
	def test_delivery_open_and_bounce_return_200_and_change_no_state(self):
		subscriber = self._make_subscriber(
			"passthrough@example.com", lists=[self.list_a]
		)

		for record_type in ("Delivery", "Open", "Bounce"):
			response = self._post(
				{
					"RecordType": record_type,
					"Recipient": "passthrough@example.com",
					"MessageStream": "broadcast",
				}
			)
			self.assertEqual(response.status_code, 200)

		subscriber.refresh_from_db()
		self.assertTrue(subscriber.active)
		self.assertEqual(SuppressionEvent.objects.count(), 0)

	@mock.patch.dict(os.environ, WEBHOOK_ENV)
	def test_subscription_change_deactivates_via_the_full_http_path(self):
		subscriber = self._make_subscriber("http-path@example.com", lists=[self.list_a])
		payload = _subscription_change_payload(
			"http-path@example.com", True, reason="HardBounce"
		)
		response = self._post(payload)

		self.assertEqual(response.status_code, 200)
		subscriber.refresh_from_db()
		self.assertFalse(subscriber.active)

	@mock.patch.dict(os.environ, WEBHOOK_ENV)
	def test_unrecognised_record_type_returns_200(self):
		response = self._post({"RecordType": "SomethingPostmarkMightAddLater"})
		self.assertEqual(response.status_code, 200)

	@mock.patch.dict(os.environ, WEBHOOK_ENV)
	def test_malformed_json_body_returns_400(self):
		response = self.client.post(
			self.url,
			data="not json",
			content_type="application/json",
			HTTP_AUTHORIZATION=_basic_auth_header(
				WEBHOOK_ENV["POSTMARK_WEBHOOK_USERNAME"],
				WEBHOOK_ENV["POSTMARK_WEBHOOK_PASSWORD"],
			),
		)
		self.assertEqual(response.status_code, 400)

	def test_missing_webhook_credentials_configuration_rejects_everything(self):
		# No POSTMARK_WEBHOOK_USERNAME/PASSWORD patched in -- simulates a
		# deployment that forgot to configure them. Must fail closed.
		payload = _subscription_change_payload(
			"nope@example.com", True, reason="HardBounce"
		)
		with mock.patch.dict(os.environ, {}, clear=False):
			os.environ.pop("POSTMARK_WEBHOOK_USERNAME", None)
			os.environ.pop("POSTMARK_WEBHOOK_PASSWORD", None)
			response = self._post(payload, authorized=False)
		self.assertEqual(response.status_code, 403)

	@mock.patch.dict(os.environ, WEBHOOK_ENV)
	def test_get_is_not_allowed(self):
		response = self.client.get(self.url)
		self.assertEqual(response.status_code, 405)
