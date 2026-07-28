"""
Tests for EmailContentOrganizer.organize_trials' recruiting/not-recruiting split.

Regression coverage for the bug where the split matched
`"recruit" in str(t.recruitment_status).lower()` against the raw registry string,
which also matches "Not Recruiting", "NOT_YET_RECRUITING", "ACTIVE_NOT_RECRUITING",
"Ongoing, recruitment ended" and "Authorised, recruitment pending". The fix splits
on `recruitment_status_normalized == "recruiting"` instead, and treats a NULL
normalized status (the normalizer didn't recognise the raw value) as not-recruiting
rather than guessing from the raw string.
"""

import os
from datetime import timedelta

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")

import django

django.setup()

from django.test import TestCase
from django.utils import timezone

from gregory.models import Trials
from templates.emails.components.content_organizer import EmailContentOrganizer


class TestOrganizeTrialsRecruitmentSplit(TestCase):
	def setUp(self):
		self.organizer = EmailContentOrganizer(email_type="weekly_summary")

	def _make_trial(self, title, raw_status, days_ago=1):
		return Trials.objects.create(
			title=title,
			link=f"https://example.com/trials/{title.replace(' ', '-').lower()}",
			discovery_date=timezone.now() - timedelta(days=days_ago),
			recruitment_status=raw_status,
		)

	# ── Regressions: raw strings that used to false-match "recruit" ──────────

	def test_not_recruiting_lands_in_regular(self):
		trial = self._make_trial("Not Recruiting Trial", "Not Recruiting")
		result = self.organizer.organize_trials([trial])
		self.assertIn(trial, result["regular_trials"])
		self.assertNotIn(trial, result["featured_trials"])

	def test_not_yet_recruiting_lands_in_regular(self):
		trial = self._make_trial("Not Yet Recruiting Trial", "NOT_YET_RECRUITING")
		result = self.organizer.organize_trials([trial])
		self.assertIn(trial, result["regular_trials"])
		self.assertNotIn(trial, result["featured_trials"])

	def test_active_not_recruiting_lands_in_regular(self):
		trial = self._make_trial("Active Not Recruiting Trial", "ACTIVE_NOT_RECRUITING")
		result = self.organizer.organize_trials([trial])
		self.assertIn(trial, result["regular_trials"])
		self.assertNotIn(trial, result["featured_trials"])

	def test_ongoing_recruitment_ended_lands_in_regular(self):
		trial = self._make_trial(
			"Ongoing Recruitment Ended Trial", "Ongoing, recruitment ended"
		)
		result = self.organizer.organize_trials([trial])
		self.assertIn(trial, result["regular_trials"])
		self.assertNotIn(trial, result["featured_trials"])

	def test_authorised_recruitment_pending_lands_in_regular(self):
		trial = self._make_trial(
			"Authorised Recruitment Pending Trial", "Authorised, recruitment pending"
		)
		result = self.organizer.organize_trials([trial])
		self.assertIn(trial, result["regular_trials"])
		self.assertNotIn(trial, result["featured_trials"])

	# ── Genuinely recruiting raw/normalized pairs land in featured ───────────

	def test_recruiting_variants_land_in_featured(self):
		raw_values = [
			"RECRUITING",
			"Recruiting",
			"Ongoing, recruiting",
			"Authorised, recruiting",
		]
		for raw in raw_values:
			with self.subTest(raw=raw):
				trial = self._make_trial(f"Trial {raw}", raw)
				result = self.organizer.organize_trials([trial])
				self.assertIn(trial, result["featured_trials"])
				self.assertNotIn(trial, result["regular_trials"])

	# ── Unset normalized status: treated as not-recruiting, no guessing ──────

	def test_unrecognised_raw_status_treated_as_not_recruiting(self):
		# A raw value the normalizer doesn't recognise as "recruiting" but that
		# still contains the substring "recruit" — this is exactly the case the
		# old substring match got wrong.
		trial = self._make_trial(
			"Weird Status Trial", "Some Unrecognised Recruit Status"
		)
		result = self.organizer.organize_trials([trial])
		self.assertNotEqual(trial.recruitment_status_normalized, "recruiting")
		self.assertNotIn(trial, result["featured_trials"])
		self.assertIn(trial, result["regular_trials"])

	def test_blank_raw_status_treated_as_not_recruiting(self):
		trial = self._make_trial("No Status Trial", None)
		self.assertIsNone(trial.recruitment_status_normalized)
		result = self.organizer.organize_trials([trial])
		self.assertNotIn(trial, result["featured_trials"])
		self.assertIn(trial, result["regular_trials"])

	# ── content_stats.recruiting_trials only counts genuinely recruiting ────

	def test_content_stats_recruiting_count_excludes_misclassified(self):
		recruiting = self._make_trial("Recruiting Trial", "Recruiting")
		not_recruiting = self._make_trial("Not Recruiting Trial 2", "Not Recruiting")
		not_yet = self._make_trial("Not Yet Trial", "NOT_YET_RECRUITING")

		organized_trials = self.organizer.organize_trials(
			[recruiting, not_recruiting, not_yet]
		)
		content_stats = self.organizer.get_content_statistics(
			{
				"total_count": 0,
				"high_confidence_count": 0,
				"featured_articles": [],
				"regular_articles": [],
			},
			organized_trials,
		)
		self.assertEqual(content_stats["recruiting_trials"], 1)
