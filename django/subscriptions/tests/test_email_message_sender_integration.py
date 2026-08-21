"""
End-to-end check that record_sent_message is actually wired into the real
send loops, not just correct in isolation (see test_send_email_metadata.py
for the isolated unit tests). Exercises subscriptions.utils.announcement_send
.send_announcement, which shares its send_email() call with both the admin
"Send" action and the send_announcement management command — the same
fixture pattern as test_announcement_send_resume.py.
"""

from unittest.mock import MagicMock, patch

from django.contrib.sites.models import Site
from django.test import TestCase

from gregory.models import Team
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import (
	Announcement,
	EmailMessage,
	Lists,
	ListSubscription,
	Subscribers,
)
from subscriptions.utils.announcement_send import send_announcement

SEND_EMAIL_TARGET = "subscriptions.utils.announcement_send.send_email"


def _ok_response():
	r = MagicMock()
	r.status_code = 200
	r.json.return_value = {"MessageID": "postmark-message-id"}
	return r


def _connection_error(*args, **kwargs):
	import requests

	raise requests.exceptions.ConnectionError("boom")


class SendAnnouncementRecordsEmailMessageTest(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.org = Organization.objects.create(name="Email Message Org")
		cls.team = Team.objects.create(
			organization=cls.org, name="Email Message Team", slug="email-message-team"
		)
		cls.site = Site.objects.get_or_create(
			id=31, defaults={"domain": "emailmessage.example.com", "name": "EM"}
		)[0]
		cls.cs = CustomSetting.objects.create(
			site=cls.site,
			title="EM CS",
			api_domain="api.emailmessage.example.com",
		)
		cls.lst = Lists.objects.create(
			list_name="EM List", team=cls.team, site=cls.site
		)
		cls.subscriber = Subscribers.objects.create(
			first_name="Sub", last_name="One", email="sub@example.com", active=True
		)
		ListSubscription.objects.create(
			subscriber=cls.subscriber, list=cls.lst, is_active=True
		)
		cls.announcement = Announcement.objects.create(
			subject="Integration Announcement",
			body="<p>Body</p>",
			status="draft",
			organization=cls.org,
		)
		cls.announcement.lists.add(cls.lst)

	def test_successful_send_writes_an_email_message_row(self):
		with patch(SEND_EMAIL_TARGET, return_value=_ok_response()):
			send_announcement(self.announcement)

		self.assertEqual(EmailMessage.objects.count(), 1)
		message = EmailMessage.objects.get()
		self.assertEqual(message.recipient, "sub@example.com")
		self.assertEqual(message.tag, "announcement")
		self.assertTrue(message.accepted)
		self.assertEqual(message.message_id, "postmark-message-id")
		# NULL, not 0. classify_postmark_response returns (True, 0, ...) on
		# success, but error_code means "Postmark ErrorCode when not
		# accepted" — persisting the 0 would make error_code__isnull=False
		# match every successful send, so the field could never be used to
		# find failures.
		self.assertIsNone(message.error_code)
		self.assertEqual(message.error_message, "")

	def test_connection_error_still_writes_an_email_message_row(self):
		with patch(SEND_EMAIL_TARGET, side_effect=_connection_error):
			send_announcement(self.announcement)

		self.assertEqual(EmailMessage.objects.count(), 1)
		message = EmailMessage.objects.get()
		self.assertFalse(message.accepted)
