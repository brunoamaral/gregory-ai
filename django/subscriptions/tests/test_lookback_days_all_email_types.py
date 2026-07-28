"""
Tests for audit finding 13 (P2 subscriptions audit, 2026-07): `lookback_days`
must apply to all three email types, not just the weekly digest.

- get_articles_for_list(lst, days=N) honours N and defaults to 30.
- send_admin_summary and send_trials_notification now pass the list's own
  lookback_days to get_articles_for_list / get_trials_for_list instead of
  the old hardcoded 30.
- Widening the content window must widen the sent-record exclusion window
  the same way the weekly digest already does (audit finding 11) — modeled
  on test_latest_research_delta.py::SentRecordLookbackWindowTest, including
  the "fresh item" guard that keeps the assertion from passing vacuously
  when everything gets skipped.
"""

import os
from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")

import django

django.setup()

from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from gregory.models import Articles, Subject, Team, Trials
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.management.commands.utils.subscription import get_articles_for_list
from subscriptions.models import (
	Lists,
	SentArticleNotification,
	SentTrialNotification,
	Subscribers,
)


def _mock_ok_result():
	result = MagicMock(status_code=200)
	result.json.return_value = {"ErrorCode": 0, "Message": "OK"}
	return result


class GetArticlesForListDaysParamTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(
			name="Days Param Org", slug="days-param-org"
		)
		self.team = Team.objects.create(
			name="Days Param Team", organization=self.org, slug="days-param-team"
		)
		self.subject = Subject.objects.create(
			subject_name="Days Param Subject",
			team=self.team,
			subject_slug="days-param-subject",
		)
		self.lst = Lists.objects.create(list_name="Days Param List", team=self.team)
		self.lst.subjects.add(self.subject)

	def _make_article(self, title, days_ago):
		article = Articles.objects.create(
			title=title,
			link=f"https://example.com/articles/{title.replace(' ', '-').lower()}",
		)
		Articles.objects.filter(pk=article.pk).update(
			discovery_date=timezone.now() - timedelta(days=days_ago)
		)
		article.refresh_from_db()
		article.subjects.add(self.subject)
		return article

	def test_defaults_to_30_days(self):
		old = self._make_article("45 Day Article Default", 45)
		self.assertNotIn(old, get_articles_for_list(self.lst))

	def test_days_param_widens_window(self):
		old = self._make_article("45 Day Article Wide", 45)
		self.assertIn(old, get_articles_for_list(self.lst, days=60))

	def test_days_param_narrows_window(self):
		recent = self._make_article("10 Day Article Narrow", 10)
		self.assertNotIn(recent, get_articles_for_list(self.lst, days=8))


class _BaseLookbackCommandTestCase(TestCase):
	"""Shared fixtures: one org/team/subject/site/CustomSetting/subscriber."""

	def setUp(self):
		self.org = Organization.objects.create(name="Lookback Org", slug="lookback-org")
		self.team = Team.objects.create(
			name="Lookback Team", organization=self.org, slug="lookback-team"
		)
		self.subject = Subject.objects.create(
			subject_name="Lookback Subject",
			team=self.team,
			subject_slug="lookback-subject",
		)
		self.site = Site.objects.get_or_create(
			id=1, defaults={"domain": "testserver", "name": "Test Site"}
		)[0]
		self.custom_settings = CustomSetting.objects.get_or_create(
			site=self.site,
			defaults={
				"title": "Test Site",
				"postmark_api_token": "test-token",
				"postmark_api_url": "https://api.postmarkapp.com/email",
			},
		)[0]
		self.subscriber = Subscribers.objects.create(
			first_name="Lookback",
			last_name="Tester",
			email="lookback@example.com",
			active=True,
		)

	def _make_article(self, title, days_ago):
		article = Articles.objects.create(
			title=title,
			link=f"https://example.com/articles/{title.replace(' ', '-').lower()}",
		)
		Articles.objects.filter(pk=article.pk).update(
			discovery_date=timezone.now() - timedelta(days=days_ago)
		)
		article.refresh_from_db()
		article.subjects.add(self.subject)
		return article

	def _make_trial(self, title, days_ago):
		trial = Trials.objects.create(
			title=title,
			link=f"https://example.com/trials/{title.replace(' ', '-').lower()}",
		)
		Trials.objects.filter(pk=trial.pk).update(
			discovery_date=timezone.now() - timedelta(days=days_ago)
		)
		trial.refresh_from_db()
		trial.subjects.add(self.subject)
		return trial


class AdminSummaryLookbackContentWindowTest(_BaseLookbackCommandTestCase):
	def test_article_45_days_old_surfaced_when_lookback_60(self):
		admin_list = Lists.objects.create(
			list_name="Admin Lookback 60",
			admin_summary=True,
			team=self.team,
			lookback_days=60,
			list_email_subject="Admin Summary",
		)
		admin_list.subjects.add(self.subject)
		self.subscriber.subscriptions.add(admin_list)
		old_article = self._make_article("45 Day Admin Article", 45)

		with patch(
			"subscriptions.management.commands.send_admin_summary.send_email",
			return_value=_mock_ok_result(),
		):
			call_command("send_admin_summary", stdout=StringIO())

		self.assertTrue(
			SentArticleNotification.objects.filter(
				article=old_article, list=admin_list, subscriber=self.subscriber
			).exists()
		)

	def test_article_45_days_old_not_surfaced_when_lookback_30(self):
		admin_list = Lists.objects.create(
			list_name="Admin Lookback 30",
			admin_summary=True,
			team=self.team,
			lookback_days=30,
			list_email_subject="Admin Summary",
		)
		admin_list.subjects.add(self.subject)
		self.subscriber.subscriptions.add(admin_list)
		old_article = self._make_article("45 Day Admin Article Narrow", 45)

		with patch(
			"subscriptions.management.commands.send_admin_summary.send_email",
			return_value=_mock_ok_result(),
		) as mock_send:
			call_command("send_admin_summary", stdout=StringIO())

		self.assertFalse(
			SentArticleNotification.objects.filter(
				article=old_article, list=admin_list, subscriber=self.subscriber
			).exists()
		)
		# Nothing else qualifies either, so send_email is never even called.
		mock_send.assert_not_called()

	def test_trial_45_days_old_surfaced_when_lookback_60(self):
		admin_list = Lists.objects.create(
			list_name="Admin Trial Lookback 60",
			admin_summary=True,
			team=self.team,
			lookback_days=60,
			list_email_subject="Admin Summary",
		)
		admin_list.subjects.add(self.subject)
		self.subscriber.subscriptions.add(admin_list)
		old_trial = self._make_trial("45 Day Admin Trial", 45)

		with patch(
			"subscriptions.management.commands.send_admin_summary.send_email",
			return_value=_mock_ok_result(),
		):
			call_command("send_admin_summary", stdout=StringIO())

		self.assertTrue(
			SentTrialNotification.objects.filter(
				trial=old_trial, list=admin_list, subscriber=self.subscriber
			).exists()
		)


class TrialsNotificationLookbackContentWindowTest(_BaseLookbackCommandTestCase):
	def test_trial_45_days_old_surfaced_when_lookback_60(self):
		lst = Lists.objects.create(
			list_name="Trials Lookback 60",
			clinical_trials_notifications=True,
			team=self.team,
			lookback_days=60,
			list_email_subject="New Trials",
		)
		lst.subjects.add(self.subject)
		self.subscriber.subscriptions.add(lst)
		old_trial = self._make_trial("45 Day Trial Notif", 45)

		with patch(
			"subscriptions.management.commands.send_trials_notification.send_email",
			return_value=_mock_ok_result(),
		):
			call_command("send_trials_notification", stdout=StringIO())

		self.assertTrue(
			SentTrialNotification.objects.filter(
				trial=old_trial, list=lst, subscriber=self.subscriber
			).exists()
		)

	def test_trial_45_days_old_not_surfaced_when_lookback_30(self):
		lst = Lists.objects.create(
			list_name="Trials Lookback 30",
			clinical_trials_notifications=True,
			team=self.team,
			lookback_days=30,
			list_email_subject="New Trials",
		)
		lst.subjects.add(self.subject)
		self.subscriber.subscriptions.add(lst)
		old_trial = self._make_trial("45 Day Trial Notif Narrow", 45)

		with patch(
			"subscriptions.management.commands.send_trials_notification.send_email",
			return_value=_mock_ok_result(),
		) as mock_send:
			call_command("send_trials_notification", stdout=StringIO())

		self.assertFalse(
			SentTrialNotification.objects.filter(
				trial=old_trial, list=lst, subscriber=self.subscriber
			).exists()
		)
		mock_send.assert_not_called()


class SentRecordLookbackWindowTest(_BaseLookbackCommandTestCase):
	"""The sent-record exclusion window must be at least as wide as the
	content lookback window for the admin summary and trial notification
	commands too, or an item sent between 30 and lookback_days days ago is
	treated as unsent and re-mailed on every run (audit finding 11, reopened
	by finding 13's fix unless threshold_date is widened the same way)."""

	def test_admin_summary_article_sent_50_days_ago_excluded_from_rendered_html(self):
		admin_list = Lists.objects.create(
			list_name="Admin Sent Window 60",
			admin_summary=True,
			team=self.team,
			lookback_days=60,
			list_email_subject="Admin Summary",
		)
		admin_list.subjects.add(self.subject)
		self.subscriber.subscriptions.add(admin_list)

		# A fresh article keeps the send from being skipped outright — without
		# it, the old article being correctly excluded would leave zero new
		# content, and the assertion below would pass vacuously.
		fresh = self._make_article("Fresh Admin Article", 1)
		old_article = self._make_article("Old Sent Admin Article", 50)
		notification = SentArticleNotification.objects.create(
			article=old_article, list=admin_list, subscriber=self.subscriber
		)
		SentArticleNotification.objects.filter(pk=notification.pk).update(
			sent_at=timezone.now() - timedelta(days=50)
		)

		with patch(
			"subscriptions.management.commands.send_admin_summary.send_email",
			return_value=_mock_ok_result(),
		) as mock_send:
			call_command("send_admin_summary", stdout=StringIO())

		mock_send.assert_called_once()
		html = mock_send.call_args.kwargs["html"]
		self.assertIn(fresh.title, html)
		self.assertNotIn(old_article.title, html)

	def test_trials_notification_trial_sent_50_days_ago_excluded_from_rendered_html(
		self,
	):
		lst = Lists.objects.create(
			list_name="Trials Sent Window 60",
			clinical_trials_notifications=True,
			team=self.team,
			lookback_days=60,
			list_email_subject="New Trials",
		)
		lst.subjects.add(self.subject)
		self.subscriber.subscriptions.add(lst)

		fresh = self._make_trial("Fresh Notif Trial", 1)
		old_trial = self._make_trial("Old Sent Notif Trial", 50)
		notification = SentTrialNotification.objects.create(
			trial=old_trial, list=lst, subscriber=self.subscriber
		)
		SentTrialNotification.objects.filter(pk=notification.pk).update(
			sent_at=timezone.now() - timedelta(days=50)
		)

		with patch(
			"subscriptions.management.commands.send_trials_notification.send_email",
			return_value=_mock_ok_result(),
		) as mock_send:
			call_command("send_trials_notification", stdout=StringIO())

		mock_send.assert_called_once()
		html = mock_send.call_args.kwargs["html"]
		self.assertIn(fresh.title, html)
		self.assertNotIn(old_trial.title, html)
