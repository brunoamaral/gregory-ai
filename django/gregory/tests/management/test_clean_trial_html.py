"""Tests for the clean_trial_html management command.

Run:
	docker exec gregory python manage.py test gregory.tests.management.test_clean_trial_html
"""

import os
from io import StringIO
from unittest import mock

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.core.management import call_command
from django.test import TestCase

from gregory.models import Trials


class CleanTrialHtmlCommandTest(TestCase):
	def run_command(self, **kwargs):
		out, err = StringIO(), StringIO()
		call_command("clean_trial_html", stdout=out, stderr=err, **kwargs)
		return out.getvalue(), err.getvalue()

	def _make_trial(self, n, **fields):
		trial = Trials.objects.create(
			title=fields.pop("title", f"Trial {n}"),
			link=f"https://example.com/clean-trial-html-{n}",
		)
		if fields:
			Trials.objects.filter(pk=trial.pk).update(**fields)
			trial.refresh_from_db()
		return trial

	def test_cleans_html_from_listed_columns(self):
		trial = self._make_trial(
			1,
			inclusion_criteria="Age &gt;18<br>Confirmed diagnosis<br>",
			inclusion_gender="<br>Female: yes<br>Male: yes<br>",
			condition="<p>Multiple Sclerosis</p>",
		)
		out, _ = self.run_command()
		trial.refresh_from_db()
		self.assertEqual(trial.inclusion_criteria, "Age >18 Confirmed diagnosis")
		self.assertEqual(trial.inclusion_gender, "Female: yes Male: yes")
		self.assertEqual(trial.condition, "Multiple Sclerosis")
		self.assertIn("inclusion_criteria: 1", out)
		self.assertIn("inclusion_gender: 1", out)
		self.assertIn("condition: 1", out)

	def test_summary_is_never_touched(self):
		"""summary is intentional HTML (CTIS/EUCTR compose it deliberately) and must
		never be cleaned, even when other columns on the same row have HTML."""
		trial = self._make_trial(
			2,
			summary="<b>Trial number</b>: 123<br/>Overall status: Recruiting",
			condition="<br>Multiple Sclerosis<br>",
		)
		original_summary = trial.summary
		self.run_command()
		trial.refresh_from_db()
		self.assertEqual(trial.summary, original_summary)
		self.assertIn("<br", trial.summary)
		self.assertEqual(trial.condition, "Multiple Sclerosis")

	def test_clean_row_is_left_unchanged(self):
		trial = self._make_trial(
			3,
			inclusion_criteria="No markup here at all",
		)
		self.run_command()
		trial.refresh_from_db()
		self.assertEqual(trial.inclusion_criteria, "No markup here at all")

	def test_dry_run_writes_nothing(self):
		trial = self._make_trial(4, condition="<br>Multiple Sclerosis<br>")
		original = trial.condition
		out, _ = self.run_command(**{"dry_run": True})
		trial.refresh_from_db()
		self.assertEqual(trial.condition, original)
		self.assertIn("Would change 1 row(s)", out)

	def test_idempotent_second_run_changes_nothing(self):
		self._make_trial(5, condition="<br>Multiple Sclerosis<br>")
		self.run_command()
		out, _ = self.run_command()
		self.assertIn("Changed 0 row(s)", out)

	def test_does_not_blank_non_nullable_title(self):
		"""title is NOT NULL; if it were ever all markup, cleaning must not blank it."""
		trial = self._make_trial(6, title="<br>")
		# The command's selection query only looks at listed columns via title too,
		# so this row is picked up; title must survive unblanked.
		self.run_command()
		trial.refresh_from_db()
		self.assertEqual(trial.title, "<br>")

	def test_uses_bulk_update_not_save(self):
		"""bulk_update bypasses Trials.save() (which fans out to
		sync_trial_countries() and every field normalizer); this command must never
		call save() on a Trials instance."""
		self._make_trial(7, condition="<br>Multiple Sclerosis<br>")
		with mock.patch.object(Trials, "save", autospec=True) as mocked_save:
			self.run_command()
		mocked_save.assert_not_called()
