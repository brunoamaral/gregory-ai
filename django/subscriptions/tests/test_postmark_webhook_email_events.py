"""
Integration tests for subscriptions.views.postmark_webhook's EmailEvent
dispatch (added alongside the existing SubscriptionChange/SuppressionEvent
handling — see test_postmark_webhook.py for that, unchanged).
"""

import base64
import json
import os
from unittest import mock

from django.test import Client, TestCase

from subscriptions.models import EmailEvent, SuppressionEvent

WEBHOOK_ENV = {
	"POSTMARK_WEBHOOK_USERNAME": "webhook-user",
	"POSTMARK_WEBHOOK_PASSWORD": "webhook-pass",
}


def _basic_auth_header(username, password):
	token = base64.b64encode(f"{username}:{password}".encode()).decode()
	return f"Basic {token}"


class PostmarkWebhookEmailEventDispatchTest(TestCase):
	def setUp(self):
		self.client = Client()
		self.url = "/webhooks/"

	def _post(self, payload):
		return self.client.post(
			self.url,
			data=json.dumps(payload),
			content_type="application/json",
			HTTP_AUTHORIZATION=_basic_auth_header(
				WEBHOOK_ENV["POSTMARK_WEBHOOK_USERNAME"],
				WEBHOOK_ENV["POSTMARK_WEBHOOK_PASSWORD"],
			),
		)

	@mock.patch.dict(os.environ, WEBHOOK_ENV)
	def test_delivery_open_bounce_click_spam_each_write_one_email_event(self):
		payloads = [
			{
				"RecordType": "Delivery",
				"Recipient": "a@example.com",
				"MessageID": "id-1",
				"MessageStream": "broadcast",
				"DeliveredAt": "2026-08-01T12:00:00Z",
			},
			{
				"RecordType": "Open",
				"Recipient": "b@example.com",
				"MessageID": "id-2",
				"MessageStream": "broadcast",
				"ReceivedAt": "2026-08-01T12:00:00Z",
			},
			{
				"RecordType": "Bounce",
				"Email": "c@example.com",
				"MessageID": "id-3",
				"MessageStream": "broadcast",
				"Type": "HardBounce",
				"TypeCode": 1,
				"BouncedAt": "2026-08-01T12:00:00Z",
			},
			{
				"RecordType": "Click",
				"Recipient": "d@example.com",
				"MessageID": "id-4",
				"MessageStream": "broadcast",
				"OriginalLink": "https://example.com/x",
				"ReceivedAt": "2026-08-01T12:00:00Z",
			},
			{
				"RecordType": "SpamComplaint",
				"Email": "e@example.com",
				"MessageID": "id-5",
				"MessageStream": "broadcast",
				"Type": "SpamComplaint",
				"TypeCode": 512,
				"BouncedAt": "2026-08-01T12:00:00Z",
			},
		]
		for payload in payloads:
			response = self._post(payload)
			self.assertEqual(response.status_code, 200)

		self.assertEqual(EmailEvent.objects.count(), 5)
		recipients = set(EmailEvent.objects.values_list("recipient", flat=True))
		self.assertEqual(
			recipients,
			{"a@example.com", "b@example.com", "c@example.com", "d@example.com", "e@example.com"},
		)

	@mock.patch.dict(os.environ, WEBHOOK_ENV)
	def test_subscription_change_writes_suppression_event_and_no_email_event(self):
		"""
		SubscriptionChange must keep driving suppression state exactly as
		before (SuppressionEvent) — and must write ZERO EmailEvent rows.
		SuppressionEvent is now the sole record for this type (see
		EmailEvent's model docstring): the two were previously dual-written,
		which duplicated the record in the admin and, worse, collided on
		EmailEvent's old dedup key, since Postmark sends an all-zero
		placeholder MessageID for SubscriptionChange events with no
		originating message.
		"""
		payload = {
			"RecordType": "SubscriptionChange",
			"Recipient": "unknown-recipient@example.com",
			"SuppressSending": True,
			"SuppressionReason": "HardBounce",
			"Origin": "Recipient",
			"MessageStream": "broadcast",
			"MessageID": "00000000-0000-0000-0000-000000000000",
			"ChangedAt": "2026-08-01T12:00:00Z",
		}

		response = self._post(payload)

		self.assertEqual(response.status_code, 200)
		self.assertEqual(SuppressionEvent.objects.count(), 1)
		self.assertEqual(EmailEvent.objects.count(), 0)

	@mock.patch.dict(os.environ, WEBHOOK_ENV)
	def test_unauthenticated_post_writes_no_email_event(self):
		payload = {
			"RecordType": "Delivery",
			"Recipient": "a@example.com",
			"MessageID": "id-1",
			"MessageStream": "broadcast",
		}
		response = self.client.post(
			self.url, data=json.dumps(payload), content_type="application/json"
		)
		self.assertEqual(response.status_code, 403)
		self.assertEqual(EmailEvent.objects.count(), 0)

	@mock.patch.dict(os.environ, WEBHOOK_ENV)
	def test_unrecognised_record_type_writes_no_email_event(self):
		response = self._post({"RecordType": "SomethingPostmarkMightAddLater"})
		self.assertEqual(response.status_code, 200)
		self.assertEqual(EmailEvent.objects.count(), 0)
