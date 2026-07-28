"""
Tests for the send_announcement management command — the cron-driven worker
that picks up announcements queued by the admin "Send to Subscribers" action
(status='queued') and performs the actual Postmark send via
subscriptions.utils.announcement_send.send_announcement.
"""

from io import StringIO
from unittest.mock import MagicMock, patch

from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase

from organizations.models import Organization
from gregory.models import Team
from sitesettings.models import CustomSetting
from subscriptions.models import Announcement, ListSubscription, Lists, Subscribers

SEND_EMAIL_TARGET = "subscriptions.utils.announcement_send.send_email"


def _ok_response():
	r = MagicMock()
	r.status_code = 200
	r.json.return_value = {"MessageID": "test"}
	return r


class SendAnnouncementCommandTest(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.org = Organization.objects.create(name="Cmd Org")
		cls.team = Team.objects.create(
			organization=cls.org, name="Cmd Team", slug="cmd-team"
		)
		cls.site = Site.objects.get_or_create(
			id=40, defaults={"domain": "cmd.example.com", "name": "Cmd"}
		)[0]
		cls.site.domain = "cmd.example.com"
		cls.site.save()
		CustomSetting.objects.create(
			site=cls.site, title="Cmd CS", api_domain="api.cmd.example.com"
		)
		cls.lst = Lists.objects.create(
			list_name="Cmd List", team=cls.team, site=cls.site
		)
		cls.sub = Subscribers.objects.create(
			first_name="Kim", last_name="Lee", email="kim@example.com", active=True
		)
		ListSubscription.objects.create(
			subscriber=cls.sub, list=cls.lst, is_active=True
		)

	def _make_announcement(self, status="queued", subject="Cmd Announcement"):
		ann = Announcement.objects.create(
			subject=subject, body="<p>Body</p>", status=status, organization=self.org
		)
		ann.lists.add(self.lst)
		return ann

	def test_sends_queued_announcement(self):
		ann = self._make_announcement(status="queued")
		with patch(SEND_EMAIL_TARGET, return_value=_ok_response()) as mock_send:
			call_command("send_announcement", stdout=StringIO())
		mock_send.assert_called_once()
		ann.refresh_from_db()
		self.assertEqual(ann.status, "sent")
		self.assertEqual(ann.recipients_count, 1)

	def test_ignores_non_queued_announcements(self):
		draft = self._make_announcement(status="draft", subject="Draft")
		sent = self._make_announcement(status="sent", subject="Sent")
		with patch(
			SEND_EMAIL_TARGET,
			side_effect=AssertionError("must not send non-queued announcements"),
		):
			call_command("send_announcement", stdout=StringIO())
		draft.refresh_from_db()
		sent.refresh_from_db()
		self.assertEqual(draft.status, "draft")
		self.assertEqual(sent.status, "sent")

	def test_no_queued_announcements_is_a_no_op(self):
		out = StringIO()
		call_command("send_announcement", stdout=out)
		self.assertIn("No queued announcements", out.getvalue())
