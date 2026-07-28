"""
Tests for the P0 email-payload-size fix (audit finding 1):

- Lists.trial_max_age_days staleness filter in get_trials_for_list
- subscriptions.utils.email_limits.resolve_limits / render_within_limit
- article_limit / trial_limit truncation and rollover in the three send
  commands, and that only rendered content is recorded as sent
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
from subscriptions.management.commands.utils.subscription import get_trials_for_list
from subscriptions.models import (
	FailedNotification,
	Lists,
	SentArticleNotification,
	SentTrialNotification,
	Subscribers,
)
from subscriptions.utils.email_limits import (
	SAFE_BODY_CHARS,
	render_within_limit,
	resolve_limits,
)


def _mock_ok_result():
	result = MagicMock(status_code=200)
	result.json.return_value = {"ErrorCode": 0, "Message": "OK"}
	return result


def _mock_fail_result(status_code=500):
	result = MagicMock(status_code=status_code)
	result.json.return_value = {"ErrorCode": 300, "Message": "Failed"}
	return result


class BaseSubscriptionCommandTestCase(TestCase):
	"""Shared fixtures for lists/subscribers/site/settings."""

	def setUp(self):
		self.org = Organization.objects.create(name="Payload Org", slug="payload-org")
		self.team = Team.objects.create(
			name="Payload Team", organization=self.org, slug="payload-team"
		)
		self.subject = Subject.objects.create(
			subject_name="Payload Subject",
			team=self.team,
			subject_slug="payload-subject",
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
			first_name="Payload",
			last_name="Tester",
			email="payload@example.com",
			active=True,
		)

	def _make_trial(self, title, discovery_days_ago=0, registration_days_ago=None):
		trial = Trials.objects.create(
			title=title,
			link=f"https://example.com/trials/{title.replace(' ', '-').lower()}",
		)
		if registration_days_ago is not None:
			trial.date_registration = (
				timezone.now() - timedelta(days=registration_days_ago)
			).date()
			trial.save(update_fields=["date_registration"])
		Trials.objects.filter(pk=trial.pk).update(
			discovery_date=timezone.now() - timedelta(days=discovery_days_ago)
		)
		trial.refresh_from_db()
		trial.subjects.add(self.subject)
		return trial

	def _make_article(self, title, days_ago=0):
		article = Articles.objects.create(
			title=title,
			link=f"https://example.com/articles/{title.replace(' ', '-').lower()}",
			doi=f"10.9999/{title.replace(' ', '-').lower()}",
		)
		Articles.objects.filter(pk=article.pk).update(
			discovery_date=timezone.now() - timedelta(days=days_ago)
		)
		article.refresh_from_db()
		article.subjects.add(self.subject)
		return article


class GetTrialsForListStalenessTest(BaseSubscriptionCommandTestCase):
	def test_trial_older_than_max_age_excluded(self):
		lst = Lists.objects.create(
			list_name="Staleness List", team=self.team, trial_max_age_days=90
		)
		lst.subjects.add(self.subject)
		old_trial = self._make_trial(
			"Old Trial", discovery_days_ago=1, registration_days_ago=200
		)

		qs = get_trials_for_list(lst)
		self.assertNotIn(old_trial, qs)

	def test_trial_included_when_max_age_is_none(self):
		lst = Lists.objects.create(
			list_name="No Age Limit List", team=self.team, trial_max_age_days=None
		)
		lst.subjects.add(self.subject)
		old_trial = self._make_trial(
			"Old Trial No Limit", discovery_days_ago=1, registration_days_ago=200
		)

		qs = get_trials_for_list(lst)
		self.assertIn(old_trial, qs)

	def test_trial_with_both_dates_null_included(self):
		lst = Lists.objects.create(
			list_name="Null Dates List", team=self.team, trial_max_age_days=90
		)
		lst.subjects.add(self.subject)
		trial = self._make_trial("No Dates Trial", discovery_days_ago=1)
		self.assertIsNone(trial.date_registration)
		self.assertIsNone(trial.published_date)

		qs = get_trials_for_list(lst)
		self.assertIn(trial, qs)

	def test_published_date_used_when_date_registration_is_null(self):
		lst = Lists.objects.create(
			list_name="Published Date List", team=self.team, trial_max_age_days=90
		)
		lst.subjects.add(self.subject)

		old_trial = self._make_trial("Old Published Trial", discovery_days_ago=1)
		Trials.objects.filter(pk=old_trial.pk).update(
			published_date=timezone.now() - timedelta(days=200)
		)

		recent_trial = self._make_trial("Recent Published Trial", discovery_days_ago=1)
		Trials.objects.filter(pk=recent_trial.pk).update(
			published_date=timezone.now() - timedelta(days=10)
		)

		qs = get_trials_for_list(lst)
		self.assertNotIn(old_trial, qs)
		self.assertIn(recent_trial, qs)

	def test_july_6_incident_regression(self):
		"""
		Reproduces the production trigger: thousands of historical trials
		bulk-imported with a fresh discovery_date. Without the staleness
		filter, discovery_date alone would include the entire batch.
		"""
		lst = Lists.objects.create(
			list_name="Alzheimer Disease", team=self.team, trial_max_age_days=90
		)
		lst.subjects.add(self.subject)

		now = timezone.now()
		old_trials = [
			Trials(
				title=f"Historical Trial {i}",
				link=f"https://example.com/trials/historical-{i}",
				discovery_date=now,
				date_registration=(now - timedelta(days=365 * 10)).date(),
			)
			for i in range(3000)
		]
		Trials.objects.bulk_create(old_trials, batch_size=500)

		recent_trials = [
			Trials(
				title=f"Recent Trial {i}",
				link=f"https://example.com/trials/recent-{i}",
				discovery_date=now,
				date_registration=(now - timedelta(days=10)).date(),
			)
			for i in range(15)
		]
		Trials.objects.bulk_create(recent_trials, batch_size=50)

		through = Trials.subjects.through
		all_trials = Trials.objects.filter(
			title__startswith="Historical Trial"
		) | Trials.objects.filter(title__startswith="Recent Trial")
		through.objects.bulk_create(
			[through(trials_id=t.pk, subject_id=self.subject.pk) for t in all_trials],
			batch_size=500,
		)

		qs = get_trials_for_list(lst)
		self.assertEqual(qs.count(), 15)


class ResolveLimitsTest(TestCase):
	def test_substitutes_default_for_none(self):
		lst = MagicMock(article_limit=None, trial_limit=None)
		self.assertEqual(resolve_limits(lst), (15, 15))

	def test_substitutes_default_for_zero(self):
		lst = MagicMock(article_limit=0, trial_limit=0)
		self.assertEqual(resolve_limits(lst), (15, 15))

	def test_respects_custom_values(self):
		lst = MagicMock(article_limit=5, trial_limit=3)
		self.assertEqual(resolve_limits(lst), (5, 3))


class RenderWithinLimitTest(TestCase):
	def test_returns_first_fitting_render(self):
		calls = []

		def render(articles, trials):
			calls.append((list(articles), list(trials)))
			return "ok", articles, trials

		articles = [1, 2, 3]
		trials = ["a", "b"]
		html, used_articles, used_trials = render_within_limit(render, articles, trials)

		self.assertEqual(html, "ok")
		self.assertEqual(used_articles, articles)
		self.assertEqual(used_trials, trials)
		self.assertEqual(len(calls), 1)

	def test_halves_on_overflow(self):
		call_sizes = []

		def render(articles, trials):
			total = len(articles) + len(trials)
			call_sizes.append(total)
			if total > 4:
				html = "x" * (SAFE_BODY_CHARS + 1)
			else:
				html = "ok"
			return html, articles, trials

		articles = list(range(6))
		trials = list(range(6))
		html, used_articles, used_trials = render_within_limit(render, articles, trials)

		self.assertEqual(html, "ok")
		self.assertEqual(len(used_articles), 1)
		self.assertEqual(len(used_trials), 1)
		# 12 -> 6 -> 2: three attempts before it fits.
		self.assertEqual(call_sizes, [12, 6, 2])

	def test_gives_up_when_single_item_still_overflows(self):
		def render(articles, trials):
			return "x" * (SAFE_BODY_CHARS + 1), articles, trials

		html, used_articles, used_trials = render_within_limit(render, [1, 2], [1, 2])

		self.assertIsNone(html)
		self.assertEqual(used_articles, [])
		self.assertEqual(used_trials, [])


class SendTrialsNotificationLimitTest(BaseSubscriptionCommandTestCase):
	def setUp(self):
		super().setUp()
		self.lst = Lists.objects.create(
			list_name="Trial Notif List",
			team=self.team,
			clinical_trials_notifications=True,
			trial_limit=5,
			trial_max_age_days=None,
			list_email_subject="New Trials",
		)
		self.lst.subjects.add(self.subject)
		self.subscriber.subscriptions.add(self.lst)

	def _run(self):
		with patch(
			"subscriptions.management.commands.send_trials_notification.send_email",
			return_value=_mock_ok_result(),
		):
			out = StringIO()
			call_command("send_trials_notification", stdout=out)
			return out.getvalue()

	def test_renders_at_most_trial_limit_and_stays_under_safe_body(self):
		for i in range(400):
			self._make_trial(f"Trial {i}", discovery_days_ago=1)

		self._run()

		sent = SentTrialNotification.objects.filter(
			list=self.lst, subscriber=self.subscriber
		)
		self.assertLessEqual(sent.count(), 5)
		self.assertGreater(sent.count(), 0)

	def test_only_rendered_trials_get_sent_rows(self):
		trials = [
			self._make_trial(f"Trial {i}", discovery_days_ago=1) for i in range(20)
		]

		self._run()

		sent_ids = set(
			SentTrialNotification.objects.filter(
				list=self.lst, subscriber=self.subscriber
			).values_list("trial_id", flat=True)
		)
		self.assertEqual(len(sent_ids), 5)
		self.assertTrue(sent_ids.issubset({t.pk for t in trials}))

	def test_rollover_second_run_sends_disjoint_trials(self):
		for i in range(20):
			self._make_trial(f"Trial {i}", discovery_days_ago=1)

		self._run()
		first_ids = set(
			SentTrialNotification.objects.filter(
				list=self.lst, subscriber=self.subscriber
			).values_list("trial_id", flat=True)
		)

		self._run()
		second_ids = (
			set(
				SentTrialNotification.objects.filter(
					list=self.lst, subscriber=self.subscriber
				).values_list("trial_id", flat=True)
			)
			- first_ids
		)

		self.assertEqual(len(first_ids), 5)
		self.assertEqual(len(second_ids), 5)
		self.assertTrue(first_ids.isdisjoint(second_ids))

	def test_failed_send_writes_no_sent_notifications(self):
		for i in range(5):
			self._make_trial(f"Trial {i}", discovery_days_ago=1)

		with patch(
			"subscriptions.management.commands.send_trials_notification.send_email",
			return_value=_mock_fail_result(),
		):
			out = StringIO()
			call_command("send_trials_notification", stdout=out)

		self.assertEqual(
			SentTrialNotification.objects.filter(
				list=self.lst, subscriber=self.subscriber
			).count(),
			0,
		)
		self.assertTrue(
			FailedNotification.objects.filter(
				list=self.lst, subscriber=self.subscriber
			).exists()
		)


class SendAdminSummaryLimitTest(BaseSubscriptionCommandTestCase):
	def setUp(self):
		super().setUp()
		self.lst = Lists.objects.create(
			list_name="Admin Summary List",
			team=self.team,
			admin_summary=True,
			article_limit=3,
			trial_limit=3,
			trial_max_age_days=None,
			list_email_subject="Admin Summary",
		)
		self.lst.subjects.add(self.subject)
		self.subscriber.subscriptions.add(self.lst)

	def _run(self):
		with patch(
			"subscriptions.management.commands.send_admin_summary.send_email",
			return_value=_mock_ok_result(),
		):
			out = StringIO()
			call_command("send_admin_summary", stdout=out)
			return out.getvalue()

	def test_truncates_articles_and_trials_ordered_newest_first_and_records_only_rendered(
		self,
	):
		articles = [
			self._make_article(f"Article {i}", days_ago=i + 1) for i in range(10)
		]
		trials = [
			self._make_trial(f"Trial {i}", discovery_days_ago=i + 1) for i in range(10)
		]

		self._run()

		sent_articles = SentArticleNotification.objects.filter(
			list=self.lst, subscriber=self.subscriber
		)
		sent_trials = SentTrialNotification.objects.filter(
			list=self.lst, subscriber=self.subscriber
		)
		self.assertEqual(sent_articles.count(), 3)
		self.assertEqual(sent_trials.count(), 3)

		# Newest-first ordering means the 3 most-recently-discovered items win.
		newest_article_ids = {
			a.pk
			for a in sorted(articles, key=lambda a: a.discovery_date, reverse=True)[:3]
		}
		newest_trial_ids = {
			t.pk
			for t in sorted(trials, key=lambda t: t.discovery_date, reverse=True)[:3]
		}
		self.assertEqual(
			set(sent_articles.values_list("article_id", flat=True)), newest_article_ids
		)
		self.assertEqual(
			set(sent_trials.values_list("trial_id", flat=True)), newest_trial_ids
		)


class SendWeeklySummaryTrialLimitTest(BaseSubscriptionCommandTestCase):
	def setUp(self):
		super().setUp()
		self.lst = Lists.objects.create(
			list_name="Weekly Digest List",
			team=self.team,
			weekly_digest=True,
			article_sort_order="date",
			trial_limit=4,
			trial_max_age_days=None,
			lookback_days=30,
			list_email_subject="Weekly Digest",
		)
		self.lst.subjects.add(self.subject)
		self.subscriber.subscriptions.add(self.lst)

	def _run(self):
		with patch(
			"subscriptions.management.commands.send_weekly_summary.send_email",
			return_value=_mock_ok_result(),
		):
			out = StringIO()
			call_command("send_weekly_summary", stdout=out)
			return out.getvalue()

	def test_truncates_trials_and_records_only_rendered(self):
		trials = [
			self._make_trial(f"Trial {i}", discovery_days_ago=i + 1) for i in range(12)
		]

		self._run()

		sent_trials = SentTrialNotification.objects.filter(
			list=self.lst, subscriber=self.subscriber
		)
		self.assertEqual(sent_trials.count(), 4)
		self.assertTrue(
			set(sent_trials.values_list("trial_id", flat=True)).issubset(
				{t.pk for t in trials}
			)
		)
