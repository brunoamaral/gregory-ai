"""
Tests for the incremental ClinicalTrials.gov fetch window (pipeline audit, item 1.1).

Covers:
  - _build_search_params: no date filter without an anchor; RANGE filter with a
    2-day overlap when Sources.last_successful_fetch_at is set
  - anchor semantics: advanced only after a fully successful fetch; never on a
    fetch error, on hitting the result cap, or when items fail to process
  - a parse failure on the first study does not abort the source (clinical_trial
    is pre-bound for the exception handlers)

Run:
  docker exec gregory python manage.py test gregory.tests.test_ctgov_incremental_fetch
"""

import os
from datetime import timedelta
from unittest.mock import MagicMock

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import TestCase
from django.utils import timezone

from gregory.classes import ClinicalTrial
from gregory.management.commands.feedreader_trials_ctgov import Command
from gregory.models import Sources, Trials


def make_command(api=None):
	cmd = Command()
	cmd.debug = False
	cmd.api = api if api is not None else MagicMock()
	return cmd


def make_trial(nct="NCT00000001", title="Test trial title"):
	return ClinicalTrial(
		title=title,
		summary="A summary",
		link=f"https://clinicaltrials.gov/study/{nct}",
		published_date=None,
		identifiers={"nct": nct},
		extra_fields={},
	)


class BuildSearchParamsTests(TestCase):
	def setUp(self):
		self.source = Sources.objects.create(
			name="CTGov Test",
			method="ctgov_api",
			source_for="trials",
			active=True,
			ctgov_search_condition="multiple sclerosis",
		)
		self.cmd = make_command()

	def test_no_anchor_means_no_date_filter(self):
		params = self.cmd._build_search_params(self.source)
		self.assertNotIn("filter_advanced", params)
		self.assertEqual(params["query_cond"], "multiple sclerosis")

	def test_anchor_produces_range_filter_with_two_day_overlap(self):
		anchor = timezone.now()
		self.source.last_successful_fetch_at = anchor
		params = self.cmd._build_search_params(self.source)
		expected_start = (anchor - timedelta(days=2)).strftime("%Y-%m-%d")
		self.assertEqual(
			params["filter_advanced"],
			f"AREA[LastUpdatePostDate]RANGE[{expected_start},MAX]",
		)


class AnchorAdvanceTests(TestCase):
	def setUp(self):
		self.source = Sources.objects.create(
			name="CTGov Test",
			method="ctgov_api",
			source_for="trials",
			active=True,
			ctgov_search_condition="multiple sclerosis",
		)

	def refreshed_anchor(self):
		self.source.refresh_from_db()
		return self.source.last_successful_fetch_at

	def test_successful_fetch_advances_anchor_to_fetch_start(self):
		api = MagicMock()
		api.search_all.return_value = iter([{"study": 1}])
		api.parse_study_to_clinical_trial.return_value = make_trial()
		cmd = make_command(api)

		before = timezone.now()
		cmd.process_sources(max_results=2000)
		anchor = self.refreshed_anchor()

		self.assertIsNotNone(anchor)
		self.assertGreaterEqual(anchor, before)
		self.assertEqual(Trials.objects.count(), 1)

	def test_fetch_error_does_not_advance_anchor(self):
		api = MagicMock()
		api.search_all.side_effect = Exception("connection dropped mid-pagination")
		cmd = make_command(api)

		cmd.process_sources(max_results=2000)

		self.assertIsNone(self.refreshed_anchor())

	def test_hitting_result_cap_does_not_advance_anchor(self):
		api = MagicMock()
		api.search_all.return_value = iter([{"study": 1}])
		api.parse_study_to_clinical_trial.return_value = make_trial()
		cmd = make_command(api)

		# One study fetched with a cap of one: a capped run is not a complete run.
		cmd.process_sources(max_results=1)

		self.assertIsNone(self.refreshed_anchor())
		# The trial itself is still stored — only the anchor is held back.
		self.assertEqual(Trials.objects.count(), 1)

	def test_item_error_does_not_advance_anchor(self):
		api = MagicMock()
		api.search_all.return_value = iter([{"study": 1}])
		api.parse_study_to_clinical_trial.side_effect = Exception("bad study payload")
		cmd = make_command(api)

		cmd.process_sources(max_results=2000)

		self.assertIsNone(self.refreshed_anchor())

	def test_parse_failure_on_first_study_does_not_abort_source(self):
		"""A parse error must be handled as an item error, not crash the source loop."""
		api = MagicMock()
		api.search_all.return_value = iter([{"study": 1}, {"study": 2}])
		api.parse_study_to_clinical_trial.side_effect = [
			Exception("bad study payload"),
			make_trial(nct="NCT00000002", title="Second trial"),
		]
		cmd = make_command(api)

		cmd.process_sources(max_results=2000)

		# The second study was still processed after the first one failed.
		self.assertEqual(Trials.objects.count(), 1)
		self.assertIsNone(self.refreshed_anchor())

	def test_incremental_window_passed_to_api(self):
		anchor = timezone.now() - timedelta(days=1)
		self.source.last_successful_fetch_at = anchor
		self.source.save(update_fields=["last_successful_fetch_at"])

		api = MagicMock()
		api.search_all.return_value = iter([])
		cmd = make_command(api)

		cmd.process_sources(max_results=2000)

		_, kwargs = api.search_all.call_args
		expected_start = (anchor - timedelta(days=2)).strftime("%Y-%m-%d")
		self.assertEqual(
			kwargs.get("filter_advanced"),
			f"AREA[LastUpdatePostDate]RANGE[{expected_start},MAX]",
		)
		self.assertEqual(kwargs.get("max_results"), 2000)
