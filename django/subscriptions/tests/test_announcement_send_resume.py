"""
Tests for subscriptions.utils.announcement_send.send_announcement — the
idempotent, suppression-aware, resumable send loop that replaced the
synchronous loop that used to live directly in AnnouncementAdmin.send_view.

Covers audit finding 12 (P2 subscriptions audit, 2026-07): a single
suppressed recipient used to mark the whole announcement "failed", and
retrying a "failed" announcement re-mailed everyone who had already
succeeded. Two live prod announcements (#9, #12) hit exactly this trap.
"""

import requests
from unittest.mock import MagicMock, patch

from django.contrib.admin import site as admin_site
from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.contrib.sites.models import Site
from django.urls import reverse

from organizations.models import Organization
from gregory.models import Team
from sitesettings.models import CustomSetting
from subscriptions.admin import AnnouncementAdmin
from subscriptions.models import (
	Announcement,
	AnnouncementRecipient,
	Lists,
	ListSubscription,
	Subscribers,
)
from subscriptions.utils.announcement_send import send_announcement

SEND_EMAIL_TARGET = "subscriptions.utils.announcement_send.send_email"


def _ok_response():
	r = MagicMock()
	r.status_code = 200
	r.json.return_value = {"MessageID": "test"}
	return r


def _suppressed_response():
	r = MagicMock()
	r.status_code = 422
	r.json.return_value = {
		"ErrorCode": 406,
		"Message": "carol@example.com is marked as inactive.",
	}
	return r


def _generic_failure_response():
	r = MagicMock()
	r.status_code = 500
	r.json.return_value = {"ErrorCode": 0, "Message": "Internal error"}
	return r


class _SendAnnouncementBase(TestCase):
	"""Shared fixtures: one org/team/site/CustomSetting/list."""

	@classmethod
	def setUpTestData(cls):
		cls.org = Organization.objects.create(name="Resume Org")
		cls.team = Team.objects.create(
			organization=cls.org, name="Resume Team", slug="resume-team"
		)
		cls.site = Site.objects.get_or_create(
			id=30, defaults={"domain": "resume.example.com", "name": "Resume"}
		)[0]
		cls.site.domain = "resume.example.com"
		cls.site.save()
		cls.cs = CustomSetting.objects.create(
			site=cls.site,
			title="Resume CS",
			api_domain="api.resume.example.com",
		)
		cls.lst = Lists.objects.create(
			list_name="Resume List", team=cls.team, site=cls.site
		)

	def _make_subscriber(self, email, first_name="Sub"):
		sub = Subscribers.objects.create(
			first_name=first_name, last_name="Test", email=email, active=True
		)
		ListSubscription.objects.create(subscriber=sub, list=self.lst, is_active=True)
		return sub

	def _make_announcement(self, status="draft"):
		ann = Announcement.objects.create(
			subject="Resume Announcement",
			body="<p>Body</p>",
			status=status,
			organization=self.org,
		)
		ann.lists.add(self.lst)
		return ann


class DuplicateSendSkipsAlreadySuccessfulTest(_SendAnnouncementBase):
	"""Regression for the 177-duplicate trap: a subscriber already recorded
	as a successful AnnouncementRecipient must not be sent to again."""

	def test_already_successful_recipient_is_not_resent(self):
		sub_done = self._make_subscriber("alice@example.com")
		sub_pending = self._make_subscriber("bob@example.com")
		ann = self._make_announcement()
		AnnouncementRecipient.objects.create(
			announcement=ann, subscriber=sub_done, list=self.lst, success=True
		)

		with patch(SEND_EMAIL_TARGET, return_value=_ok_response()) as mock_send:
			summary = send_announcement(ann)

		mock_send.assert_called_once()
		_, kwargs = mock_send.call_args
		self.assertEqual(kwargs["to"], "bob@example.com")
		self.assertEqual(summary["skipped"], 1)
		self.assertEqual(summary["sent"], 2)  # 1 pre-existing + 1 sent now
		self.assertEqual(
			AnnouncementRecipient.objects.filter(announcement=ann).count(), 2
		)


class SuppressionHandlingTest(_SendAnnouncementBase):
	def test_406_deactivates_subscriber_and_records_suppressed(self):
		sub = self._make_subscriber("carol@example.com")
		ann = self._make_announcement()

		with patch(SEND_EMAIL_TARGET, return_value=_suppressed_response()):
			summary = send_announcement(ann)

		sub.refresh_from_db()
		self.assertFalse(sub.active)
		recipient = AnnouncementRecipient.objects.get(announcement=ann, subscriber=sub)
		self.assertFalse(recipient.success)
		self.assertTrue(recipient.suppressed)
		self.assertEqual(summary["suppressed"], 1)
		self.assertEqual(summary["failed"], 0)

	def test_suppressed_recipient_does_not_mark_announcement_failed(self):
		sub_ok = self._make_subscriber("dave@example.com")
		sub_suppressed = self._make_subscriber("erin@example.com")
		ann = self._make_announcement()

		def _side_effect(to, **kwargs):
			return (
				_suppressed_response() if to == "erin@example.com" else _ok_response()
			)

		with patch(SEND_EMAIL_TARGET, side_effect=_side_effect):
			send_announcement(ann)

		ann.refresh_from_db()
		self.assertEqual(ann.status, "sent")
		self.assertEqual(ann.failures_count, 0)
		self.assertEqual(ann.recipients_count, 1)

	def test_genuine_failure_still_marks_announcement_failed(self):
		"""Sanity check that suppression handling doesn't swallow real failures."""
		sub = self._make_subscriber("frank@example.com")
		ann = self._make_announcement()

		with patch(SEND_EMAIL_TARGET, return_value=_generic_failure_response()):
			summary = send_announcement(ann)

		ann.refresh_from_db()
		self.assertEqual(ann.status, "failed")
		self.assertEqual(summary["failed"], 1)
		self.assertEqual(summary["suppressed"], 0)


class ConnectionErrorHandlingTest(_SendAnnouncementBase):
	def test_connection_error_records_recipient_and_continues(self):
		self._make_subscriber("first@example.com")
		self._make_subscriber("second@example.com")
		ann = self._make_announcement()

		call_count = {"n": 0}

		def _side_effect(to, **kwargs):
			call_count["n"] += 1
			if call_count["n"] == 1:
				raise requests.ConnectionError("connection refused")
			return _ok_response()

		with patch(SEND_EMAIL_TARGET, side_effect=_side_effect):
			summary = send_announcement(ann)

		self.assertEqual(call_count["n"], 2)
		self.assertEqual(summary["sent"], 1)
		self.assertEqual(summary["failed"], 1)
		failed_recipient = AnnouncementRecipient.objects.get(success=False)
		self.assertIn("Connection error", failed_recipient.error_message)


class ResumedSendCountsTest(_SendAnnouncementBase):
	"""recipients_count/failures_count must reflect all AnnouncementRecipient
	rows across every partial run, not just the most recent run's tally."""

	def test_counts_after_resume_match_recipient_rows(self):
		sub_ok = self._make_subscriber("gina@example.com")
		sub_flaky = self._make_subscriber("harry@example.com")
		ann = self._make_announcement()

		def _first_run(to, **kwargs):
			if to == "harry@example.com":
				return _generic_failure_response()
			return _ok_response()

		with patch(SEND_EMAIL_TARGET, side_effect=_first_run):
			send_announcement(ann)

		ann.refresh_from_db()
		self.assertEqual(ann.status, "failed")
		self.assertEqual(ann.recipients_count, 1)
		self.assertEqual(ann.failures_count, 1)

		# Resume: harry succeeds this time; gina must not be re-sent to.
		with patch(SEND_EMAIL_TARGET, return_value=_ok_response()) as mock_send:
			send_announcement(ann)

		mock_send.assert_called_once()
		_, kwargs = mock_send.call_args
		self.assertEqual(kwargs["to"], "harry@example.com")

		ann.refresh_from_db()
		self.assertEqual(ann.status, "sent")
		self.assertEqual(ann.recipients_count, 2)
		self.assertEqual(ann.failures_count, 0)


class StuckSendingRecoveryTest(_SendAnnouncementBase):
	"""A "sending" announcement left over from a crashed run can be reset to
	draft via the admin action and re-sent without duplicating deliveries."""

	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		cls.superuser = User.objects.create_superuser(
			username="resume_admin", password="pw", email="resume_admin@example.com"
		)

	def setUp(self):
		self.client = Client()
		self.client.force_login(self.superuser)
		self.admin = AnnouncementAdmin(Announcement, admin_site)

	def test_reset_stuck_announcement_then_resend_skips_already_sent(self):
		sub_done = self._make_subscriber("ivy@example.com")
		sub_pending = self._make_subscriber("jack@example.com")
		ann = self._make_announcement(status="sending")
		AnnouncementRecipient.objects.create(
			announcement=ann, subscriber=sub_done, list=self.lst, success=True
		)

		changelist_url = reverse("admin:subscriptions_announcement_changelist")
		self.client.post(
			changelist_url,
			{
				"action": "reset_stuck_announcements",
				"_selected_action": [str(ann.pk)],
			},
		)
		ann.refresh_from_db()
		self.assertEqual(ann.status, "draft")

		with patch(SEND_EMAIL_TARGET, return_value=_ok_response()) as mock_send:
			send_announcement(ann)

		mock_send.assert_called_once()
		_, kwargs = mock_send.call_args
		self.assertEqual(kwargs["to"], "jack@example.com")
		ann.refresh_from_db()
		self.assertEqual(ann.status, "sent")
		self.assertEqual(
			AnnouncementRecipient.objects.filter(
				announcement=ann, success=True
			).count(),
			2,
		)

	def test_reset_action_leaves_non_stuck_announcements_untouched(self):
		sent_ann = self._make_announcement(status="sent")
		reset_url_ann = self._make_announcement(status="sending")

		changelist_url = reverse("admin:subscriptions_announcement_changelist")
		self.client.post(
			changelist_url,
			{
				"action": "reset_stuck_announcements",
				"_selected_action": [str(sent_ann.pk), str(reset_url_ann.pk)],
			},
		)

		sent_ann.refresh_from_db()
		reset_url_ann.refresh_from_db()
		self.assertEqual(sent_ann.status, "sent")
		self.assertEqual(reset_url_ann.status, "draft")
