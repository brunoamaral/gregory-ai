"""Tests for gregory.utils.trial_site_sync.replace_trial_sites (TRIAL-GEOGRAPHY-PLAN.md
PR G2 §2.2) — the shared per-source replace helper that both CTIS and CTGov site
capture call, so neither can wipe the other's TrialSite rows for the same trial.

Run:
  docker exec gregory python manage.py test gregory.tests.test_trial_site_sync
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from gregory.models import Trials, TrialSite
from gregory.utils.trial_site_sync import replace_trial_sites


def _make_trial(**overrides):
	defaults = dict(title="Site Sync Test Trial", identifiers={"nct": "NCT09990001"})
	defaults.update(overrides)
	return Trials.objects.create(**defaults)


class ReplaceTrialSitesSourceIsolationTests(TestCase):
	def test_capturing_ctis_then_ctgov_leaves_both_sets_present(self):
		trial = _make_trial()
		replace_trial_sites(trial, "ctis", [{"name": "Hospital A", "city": "Lisbon"}])
		replace_trial_sites(trial, "ctgov", [{"name": "Site B", "city": "Phoenix"}])

		sites = list(trial.trial_sites.all())
		self.assertEqual(len(sites), 2)
		by_source = {tuple(s.sources): s.name for s in sites}
		self.assertEqual(by_source[("ctis",)], "Hospital A")
		self.assertEqual(by_source[("ctgov",)], "Site B")

	def test_rerunning_ctgov_replaces_only_ctgov_rows(self):
		trial = _make_trial()
		replace_trial_sites(trial, "ctis", [{"name": "Hospital A", "city": "Lisbon"}])
		replace_trial_sites(trial, "ctgov", [{"name": "Old Site", "city": "Phoenix"}])
		replace_trial_sites(trial, "ctgov", [{"name": "New Site", "city": "Tucson"}])

		sites = list(trial.trial_sites.all())
		self.assertEqual(len(sites), 2)
		names_by_source = {tuple(s.sources): s.name for s in sites}
		self.assertEqual(names_by_source[("ctis",)], "Hospital A")
		self.assertEqual(names_by_source[("ctgov",)], "New Site")

	def test_empty_rows_replaces_with_zero_rows_for_that_source_only(self):
		trial = _make_trial()
		replace_trial_sites(trial, "ctis", [{"name": "Hospital A", "city": "Lisbon"}])
		replace_trial_sites(trial, "ctgov", [{"name": "Site B", "city": "Phoenix"}])
		replace_trial_sites(trial, "ctgov", [])

		sites = list(trial.trial_sites.all())
		self.assertEqual(len(sites), 1)
		self.assertEqual(sites[0].sources, ["ctis"])

	def test_bulk_create_used_not_one_insert_per_row(self):
		trial = _make_trial()
		rows = [{"name": f"Site {i}", "city": "Lisbon"} for i in range(20)]
		with CaptureQueriesContext(connection) as ctx:
			replace_trial_sites(trial, "ctgov", rows)
		insert_queries = [q for q in ctx.captured_queries if "INSERT" in q["sql"].upper()]
		self.assertEqual(len(insert_queries), 1)
		self.assertEqual(TrialSite.objects.filter(trial=trial).count(), 20)


class TrialSiteStrTests(TestCase):
	"""Regression guard (Copilot review on PR #790): name is nullable (CTGov's
	facility is occasionally absent), so __str__ must not render "123/None"."""

	def test_str_uses_name_when_present(self):
		trial = _make_trial()
		site = TrialSite.objects.create(trial=trial, name="Site A", city="Lisbon")
		self.assertEqual(str(site), f"{trial.trial_id}/Site A")

	def test_str_falls_back_to_city_when_name_is_null(self):
		trial = _make_trial()
		site = TrialSite.objects.create(trial=trial, name=None, city="Lisbon")
		self.assertEqual(str(site), f"{trial.trial_id}/Lisbon")

	def test_str_falls_back_to_generic_label_when_name_and_city_are_null(self):
		trial = _make_trial()
		site = TrialSite.objects.create(trial=trial, name=None, city=None)
		self.assertEqual(str(site), f"{trial.trial_id}/site")
