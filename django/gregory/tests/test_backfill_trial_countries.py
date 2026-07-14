"""Tests for the backfill_trial_countries management command (Phase 1 of
TRIAL-COUNTRY-BACKFILL-PLAN.md, repo root)."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from gregory.management.commands.backfill_trial_countries import (
	CHANGE_REASON,
	Command,
)
from gregory.models import Trials


def _study(nct_id, countries=None):
	"""Build a minimal CTGov API study payload. *countries* is a list of raw country
	names to place under contactsLocationsModule.locations; omit for "no locations"."""
	protocol = {"identificationModule": {"nctId": nct_id}}
	if countries is not None:
		protocol["contactsLocationsModule"] = {
			"locations": [{"country": c} for c in countries]
		}
	return {"protocolSection": protocol}


class FakeAPI:
	"""Stand-in for ClinicalTrialsGovAPI returning canned studies (no network)."""

	studies_by_nct = {}
	calls = []

	def __init__(self):
		pass

	def search(self, filter_ids=None, **kwargs):
		type(self).calls.append(list(filter_ids))
		return {
			"studies": [
				self.studies_by_nct[nct]
				for nct in filter_ids
				if nct in self.studies_by_nct
			]
		}


@patch(
	"gregory.management.commands.backfill_trial_countries.ClinicalTrialsGovAPI", FakeAPI
)
class BackfillTrialCountriesTest(TestCase):
	def setUp(self):
		FakeAPI.studies_by_nct = {}
		FakeAPI.calls = []

	def run_command(self, **kwargs):
		out, err = StringIO(), StringIO()
		call_command(
			"backfill_trial_countries", sleep=0, stdout=out, stderr=err, **kwargs
		)
		return out.getvalue(), err.getvalue()

	def make_trial(self, nct, n=None, **extra):
		n = n if n is not None else (nct or "X")
		return Trials.objects.create(
			title=f"Trial {n}",
			link=f"https://clinicaltrials.gov/study/{n}",
			identifiers={"nct": nct},
			**extra,
		)

	def test_fills_empty_countries_and_records_change_reason(self):
		trial = self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {
			"NCT00000001": _study("NCT00000001", ["United States", "France"])
		}

		out, _ = self.run_command()

		trial.refresh_from_db()
		self.assertEqual(trial.countries, "France, United States")
		self.assertEqual(
			trial.countries_by_source, {"ctgov": "France, United States"}
		)
		self.assertEqual(
			sorted(tc.country.code for tc in trial.trial_countries.all()),
			["FR", "US"],
		)
		self.assertEqual(trial.history.first().history_change_reason, CHANGE_REASON)
		self.assertIn("Filled 1 trial rows", out)

	def test_batches_requests_by_batch_size(self):
		for i in range(1, 6):
			self.make_trial(f"NCT0000000{i}")
		FakeAPI.studies_by_nct = {
			f"NCT0000000{i}": _study(f"NCT0000000{i}", ["Japan"]) for i in range(1, 6)
		}

		self.run_command(batch_size=2)

		self.assertEqual([len(c) for c in FakeAPI.calls], [2, 2, 1])

	def test_idempotent_rerun_skips_trial_with_existing_country_data(self):
		# Already has ctgov country data recorded -> not selected, never touched, and
		# the API is never called for it.
		trial = self.make_trial(
			"NCT00000001",
			countries="Germany",
			countries_by_source={"ctgov": "Germany"},
		)
		FakeAPI.studies_by_nct = {
			"NCT00000001": _study("NCT00000001", ["United States"])
		}

		out, _ = self.run_command()

		trial.refresh_from_db()
		self.assertEqual(FakeAPI.calls, [])
		self.assertEqual(trial.countries, "Germany")
		self.assertEqual(trial.countries_by_source, {"ctgov": "Germany"})
		self.assertIn("0 NCT ids with no country data", out)

	def test_no_locations_case_leaves_trial_untouched_and_counted(self):
		trial = self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {"NCT00000001": _study("NCT00000001", [])}

		out, _ = self.run_command()

		trial.refresh_from_db()
		self.assertIsNone(trial.countries)
		self.assertIsNone(trial.countries_by_source)
		self.assertEqual(trial.trial_countries.count(), 0)
		self.assertIn("No site locations on registry: 1 NCT ids", out)

	def test_not_found_on_ctgov_is_counted_and_trial_untouched(self):
		trial = self.make_trial("NCT00000001")
		# FakeAPI.studies_by_nct left empty: the NCT id resolves to nothing.

		out, _ = self.run_command()

		trial.refresh_from_db()
		self.assertIsNone(trial.countries)
		self.assertIn("Not found on ClinicalTrials.gov: 1 NCT ids", out)

	def test_dry_run_saves_nothing(self):
		trial = self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {"NCT00000001": _study("NCT00000001", ["Japan"])}

		out, _ = self.run_command(dry_run=True)

		trial.refresh_from_db()
		self.assertIsNone(trial.countries)
		self.assertIsNone(trial.countries_by_source)
		self.assertIn("Would fill 1 trial rows", out)

	def test_limit_caps_candidates(self):
		for i in range(1, 4):
			self.make_trial(f"NCT0000000{i}")
		FakeAPI.studies_by_nct = {
			f"NCT0000000{i}": _study(f"NCT0000000{i}", ["Japan"]) for i in range(1, 4)
		}

		out, _ = self.run_command(limit=2)

		self.assertIn("2 NCT ids with no country data", out)
		self.assertIn("Filled 2 trial rows", out)

	def test_failed_batch_is_retried_then_skipped_and_others_continue(self):
		self.make_trial("NCT00000001")
		self.make_trial("NCT00000002")
		FakeAPI.studies_by_nct = {
			"NCT00000001": _study("NCT00000001", ["Spain"]),
			"NCT00000002": _study("NCT00000002", ["Italy"]),
		}
		original_search = FakeAPI.search

		def flaky_search(self, filter_ids=None, **kwargs):
			if "NCT00000001" in filter_ids:
				type(self).calls.append(list(filter_ids))
				raise RuntimeError("boom")
			return original_search(self, filter_ids=filter_ids, **kwargs)

		with patch.object(FakeAPI, "search", flaky_search):
			out, err = self.run_command(batch_size=1)

		self.assertIsNone(
			Trials.objects.get(identifiers__nct="NCT00000001").countries
		)
		self.assertEqual(
			Trials.objects.get(identifiers__nct="NCT00000002").countries, "Italy"
		)
		self.assertIn("failed 3 times", err)
		self.assertIn("rerun to retry", out)

	def test_unmapped_country_token_is_reported(self):
		trial = self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {
			"NCT00000001": _study("NCT00000001", ["Wakanda"])
		}

		out, _ = self.run_command()

		trial.refresh_from_db()
		# Still filled (raw string is stored regardless of whether it can be mapped
		# to an ISO country by the normalizer) ...
		self.assertEqual(trial.countries, "Wakanda")
		# ... but surfaced in the unmapped-token report so it can be triaged, mirroring
		# the PR #770 precedent for the normalization backfill.
		self.assertIn("1 distinct country token(s) could not be mapped", out)
		self.assertIn("'Wakanda'", out)

	def test_apply_countries_merges_preserving_other_source_keys(self):
		"""_apply_countries (the write-path helper) must merge into countries_by_source
		rather than overwrite it, so a key written by another source (e.g. WHO ICTRP's
		"ictrp") is never clobbered by the ctgov backfill."""
		trial = Trials.objects.create(
			title="Cross-registered trial",
			link="https://clinicaltrials.gov/study/NCT00000009",
			identifiers={"nct": "NCT00000009"},
			countries_by_source={"ictrp": "Japan"},
		)

		Command()._apply_countries(trial, "France, Spain")
		trial.refresh_from_db()

		self.assertEqual(
			trial.countries_by_source,
			{"ictrp": "Japan", "ctgov": "France, Spain"},
		)
		self.assertEqual(trial.countries, "France, Spain")

	def test_invalid_nct_ids_are_skipped(self):
		self.make_trial("not-an-id", n="BAD1")

		out, _ = self.run_command()

		self.assertEqual(FakeAPI.calls, [])
		self.assertIn("1 skipped as invalid", out)
