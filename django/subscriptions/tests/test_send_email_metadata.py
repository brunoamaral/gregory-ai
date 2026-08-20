"""
Tests for the send_email() additions (metadata, reply_to, track_opens,
track_links — all opt-in, defaulting to off/None so no existing sender's
Postmark payload changes) and for record_sent_message(), the helper every
sender calls right after send_email() to write the EmailMessage row.
"""

from unittest import mock

from django.contrib.sites.models import Site
from django.test import TestCase

from subscriptions.management.commands.utils.send_email import (
	record_sent_message,
	send_email,
)
from subscriptions.models import EmailMessage, Subscribers


def _mock_response(status_code=200, error_code=0, message_id="msg-123"):
	response = mock.Mock()
	response.status_code = status_code
	response.json.return_value = {
		"ErrorCode": error_code,
		"Message": "OK" if error_code == 0 else "Some error",
		"MessageID": message_id,
	}
	return response


class SendEmailPayloadGatingTest(TestCase):
	"""
	None of the four existing senders pass metadata/reply_to/track_opens/
	track_links, so their Postmark payload must be byte-for-byte identical
	to before this PR. Only a caller that explicitly opts in should see the
	new keys appear.
	"""

	def setUp(self):
		self.site = Site.objects.get_or_create(
			id=1, defaults={"domain": "testserver", "name": "Test Site"}
		)[0]

	def _send(self, **kwargs):
		with mock.patch(
			"subscriptions.management.commands.utils.send_email.requests.post"
		) as mock_post:
			mock_post.return_value = _mock_response()
			send_email(
				to="test@example.com",
				subject="Subject",
				html="<p>hi</p>",
				text="hi",
				site=self.site,
				api_token="token",
				api_url="https://api.postmark.test/email",
				**kwargs,
			)
			return mock_post.call_args.kwargs["json"]

	def test_default_call_has_no_metadata_reply_to_or_tracking_keys(self):
		payload = self._send()
		self.assertNotIn("Metadata", payload)
		self.assertNotIn("ReplyTo", payload)
		self.assertNotIn("TrackOpens", payload)
		self.assertNotIn("TrackLinks", payload)
		# Unchanged core shape.
		self.assertEqual(payload["MessageStream"], "broadcast")
		self.assertNotIn("Tag", payload)

	def test_metadata_included_only_when_passed(self):
		payload = self._send(metadata={"msg_token": "abc", "campaign": "x"})
		self.assertEqual(payload["Metadata"], {"msg_token": "abc", "campaign": "x"})

	def test_reply_to_included_only_when_passed(self):
		payload = self._send(reply_to="bruno@brain-regeneration.com")
		self.assertEqual(payload["ReplyTo"], "bruno@brain-regeneration.com")

	def test_track_opens_included_only_when_true(self):
		self.assertNotIn("TrackOpens", self._send(track_opens=False))
		self.assertTrue(self._send(track_opens=True)["TrackOpens"])

	def test_track_links_included_only_when_true(self):
		self.assertNotIn("TrackLinks", self._send(track_links=False))
		self.assertEqual(self._send(track_links=True)["TrackLinks"], "HtmlAndText")

	def test_tag_still_included_when_passed_as_before(self):
		payload = self._send(tag="weekly_summary")
		self.assertEqual(payload["Tag"], "weekly_summary")


class RecordSentMessageTest(TestCase):
	def setUp(self):
		self.site = Site.objects.get_or_create(
			id=1, defaults={"domain": "testserver", "name": "Test Site"}
		)[0]
		self.subscriber = Subscribers.objects.create(
			first_name="Test",
			last_name="Subscriber",
			email="subscriber@example.com",
		)

	def test_successful_response_writes_accepted_message(self):
		response = _mock_response(status_code=200, error_code=0, message_id="msg-abc")

		record_sent_message(
			response,
			recipient="Subscriber@Example.com",
			subject="Weekly digest",
			tag="weekly_summary",
			site=self.site,
			subscriber=self.subscriber,
		)

		self.assertEqual(EmailMessage.objects.count(), 1)
		message = EmailMessage.objects.get()
		self.assertTrue(message.accepted)
		self.assertEqual(message.message_id, "msg-abc")
		# Recipient is normalised the same way Subscribers.save() does.
		self.assertEqual(message.recipient, "subscriber@example.com")
		self.assertEqual(message.tag, "weekly_summary")
		self.assertEqual(message.site, self.site)
		self.assertEqual(message.subscriber, self.subscriber)
		self.assertEqual(message.error_message, "")
		self.assertIsNotNone(message.msg_token)

	def test_error_response_writes_not_accepted_with_error_details(self):
		response = _mock_response(status_code=422, error_code=406, message_id="")

		record_sent_message(
			response,
			recipient="suppressed@example.com",
			subject="Weekly digest",
			tag="weekly_summary",
			site=self.site,
		)

		message = EmailMessage.objects.get()
		self.assertFalse(message.accepted)
		self.assertEqual(message.error_code, 406)
		self.assertNotEqual(message.error_message, "")

	def test_none_response_connection_error_still_writes_a_row(self):
		"""
		A sender that caught requests.RequestException before any response
		came back must still be able to log the attempt.
		"""
		record_sent_message(
			None,
			recipient="unreachable@example.com",
			subject="Weekly digest",
			tag="weekly_summary",
			site=self.site,
		)

		message = EmailMessage.objects.get()
		self.assertFalse(message.accepted)
		self.assertEqual(message.message_id, "")

	def test_explicit_msg_token_is_used_instead_of_a_generated_one(self):
		import uuid

		token = uuid.uuid4()
		response = _mock_response()

		record_sent_message(
			response,
			recipient="test@example.com",
			subject="Subject",
			tag="author_outreach",
			site=self.site,
			msg_token=token,
		)

		message = EmailMessage.objects.get()
		self.assertEqual(message.msg_token, token)

	def test_failure_in_recording_never_raises(self):
		"""
		record_sent_message must be failure-tolerant: even if writing the
		row itself blows up, callers (mid-send-loop) must not see an
		exception propagate.
		"""
		with mock.patch(
			"subscriptions.models.EmailMessage.objects.create",
			side_effect=RuntimeError("boom"),
		):
			try:
				record_sent_message(
					_mock_response(),
					recipient="test@example.com",
					subject="Subject",
					tag="weekly_summary",
					site=self.site,
				)
			except Exception as e:  # pragma: no cover - failure path
				self.fail(f"record_sent_message must not raise, raised {e!r}")

		self.assertEqual(EmailMessage.objects.count(), 0)
