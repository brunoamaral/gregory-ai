"""
Tests for the weekly-digest staleness gap (P0 follow-up):

send_weekly_summary previously built its trials query inline and never called
get_trials_for_list, so Lists.trial_max_age_days never applied to weekly
digests. See docs/subscriptions-p0-fix-plan.md, "Task 1".
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

from gregory.models import Subject, Team, Trials
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import Lists, SentTrialNotification, Subscribers


def _mock_ok_result():
	result = MagicMock(status_code=200)
	result.json.return_value = {"ErrorCode": 0, "Message": "OK"}
	return result


class WeeklyDigestStalenessTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(
			name="Staleness Org", slug="staleness-org"
		)
		self.team = Team.objects.create(
			name="Staleness Team", organization=self.org, slug="staleness-team"
		)
		self.subject = Subject.objects.create(
			subject_name="Staleness Subject",
			team=self.team,
			subject_slug="staleness-subject",
		)
		self.site = Site.objects.get_or_create(
			id=1, defaults={"domain": "testserver", "name": "Test Site"}
		)[0]
		CustomSetting.objects.get_or_create(
			site=self.site,
			defaults={
				"title": "Test Site",
				"postmark_api_token": "test-token",
				"postmark_api_url": "https://api.postmarkapp.com/email",
			},
		)
		self.subscriber = Subscribers.objects.create(
			first_name="Staleness",
			last_name="Tester",
			email="staleness@example.com",
			active=True,
		)

	def _make_trial(self, title, discovery_days_ago=1, registration_days_ago=None):
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

	def _make_list(self, **kwargs):
		defaults = dict(
			list_name="Weekly Digest List",
			team=self.team,
			weekly_digest=True,
			article_sort_order="date",
			trial_limit=50,
			lookback_days=30,
			list_email_subject="Weekly Digest",
		)
		defaults.update(kwargs)
		lst = Lists.objects.create(**defaults)
		lst.subjects.add(self.subject)
		self.subscriber.subscriptions.add(lst)
		return lst

	def _run(self, **options):
		with patch(
			"subscriptions.management.commands.send_weekly_summary.send_email",
			return_value=_mock_ok_result(),
		):
			out = StringIO()
			call_command("send_weekly_summary", stdout=out, **options)
			return out.getvalue()

	def _sent_trial_ids(self, lst):
		return set(
			SentTrialNotification.objects.filter(
				list=lst, subscriber=self.subscriber
			).values_list("trial_id", flat=True)
		)

	def test_stale_trial_excluded_from_weekly_digest(self):
		"""
		The evidence case: a trial registered 200 days ago but discovered
		yesterday. At the default trial_max_age_days=90 it must not reach the
		digest. Fails against the pre-fix inline query, which ignores
		trial_max_age_days entirely.
		"""
		lst = self._make_list(trial_max_age_days=90)
		stale_trial = self._make_trial(
			"Stale Trial", discovery_days_ago=1, registration_days_ago=200
		)

		self._run()

		self.assertNotIn(stale_trial.pk, self._sent_trial_ids(lst))

	def test_recent_trial_included_in_weekly_digest(self):
		lst = self._make_list(trial_max_age_days=90)
		recent_trial = self._make_trial(
			"Recent Trial", discovery_days_ago=1, registration_days_ago=7
		)

		self._run()

		self.assertIn(recent_trial.pk, self._sent_trial_ids(lst))

	def test_trial_with_no_dates_included(self):
		lst = self._make_list(trial_max_age_days=90)
		undated_trial = self._make_trial("Undated Trial", discovery_days_ago=1)
		self.assertIsNone(undated_trial.date_registration)
		self.assertIsNone(undated_trial.published_date)

		self._run()

		self.assertIn(undated_trial.pk, self._sent_trial_ids(lst))

	def test_trial_max_age_days_none_disables_filter(self):
		lst = self._make_list(trial_max_age_days=None)
		stale_trial = self._make_trial(
			"Ancient Trial", discovery_days_ago=1, registration_days_ago=3000
		)

		self._run()

		self.assertIn(stale_trial.pk, self._sent_trial_ids(lst))

	def test_lookback_days_still_honoured(self):
		"""
		Guards against the helper's 30-day discovery-window default leaking in
		at the call site. Must fail if send_weekly_summary calls
		get_trials_for_list(digest_list) without days=days_to_look_back.
		"""
		lst = self._make_list(trial_max_age_days=None, lookback_days=60)
		trial = self._make_trial(
			"Old Discovery Trial", discovery_days_ago=45, registration_days_ago=1
		)

		self._run()

		self.assertIn(trial.pk, self._sent_trial_ids(lst))

	def test_cli_days_override_beats_lookback_days(self):
		lst = self._make_list(trial_max_age_days=None, lookback_days=5)
		trial = self._make_trial(
			"Overridden Trial", discovery_days_ago=45, registration_days_ago=1
		)

		self._run(days=60)

		self.assertIn(trial.pk, self._sent_trial_ids(lst))
