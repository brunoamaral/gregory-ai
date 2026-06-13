"""Tests for the backfill_trial_acronyms management command."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from gregory.management.commands.backfill_trial_acronyms import CHANGE_REASON
from gregory.models import Trials


def _study(nct_id, acronym=None):
	ident = {"nctId": nct_id}
	if acronym is not None:
		ident["acronym"] = acronym
	return {"protocolSection": {"identificationModule": ident}}


class FakeAPI:
	"""Stand-in for ClinicalTrialsGovAPI returning canned studies."""

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
	"gregory.management.commands.backfill_trial_acronyms.ClinicalTrialsGovAPI", FakeAPI
)
class BackfillTrialAcronymsTest(TestCase):
	def setUp(self):
		FakeAPI.studies_by_nct = {}
		FakeAPI.calls = []

	def run_command(self, **kwargs):
		out, err = StringIO(), StringIO()
		call_command(
			"backfill_trial_acronyms", sleep=0, stdout=out, stderr=err, **kwargs
		)
		return out.getvalue(), err.getvalue()

	def make_trial(self, nct, acronym=None, identifiers=None, n=None):
		n = n if n is not None else (nct or "X")
		return Trials.objects.create(
			title=f"Trial {n}",
			link=f"https://clinicaltrials.gov/study/{n}",
			identifiers=identifiers if identifiers is not None else {"nct": nct},
			acronym=acronym,
		)

	def test_fills_empty_acronyms_and_records_change_reason(self):
		trial = self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {"NCT00000001": _study("NCT00000001", "  OPERA ")}

		out, _ = self.run_command()

		trial.refresh_from_db()
		self.assertEqual(trial.acronym, "OPERA")
		self.assertEqual(trial.history.first().history_change_reason, CHANGE_REASON)
		self.assertIn("Updated 1 trial rows", out)

	def test_skips_trials_with_acronym_or_without_nct(self):
		self.make_trial("NCT00000001", acronym="KEEP")
		self.make_trial(None, identifiers={"euctr": "2016-001005-36"}, n="EUCTR1")
		FakeAPI.studies_by_nct = {"NCT00000001": _study("NCT00000001", "CLOBBER")}

		out, _ = self.run_command()

		self.assertEqual(FakeAPI.calls, [])
		self.assertIn("0 NCT ids with no acronym", out)
		self.assertEqual(
			Trials.objects.get(identifiers__nct="NCT00000001").acronym, "KEEP"
		)

	def test_registry_without_acronym_leaves_trial_untouched(self):
		trial = self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {"NCT00000001": _study("NCT00000001")}

		out, _ = self.run_command()

		trial.refresh_from_db()
		self.assertIsNone(trial.acronym)
		self.assertIn("No acronym on registry: 1 NCT ids", out)

	def test_nct_missing_from_response_is_counted(self):
		trial = self.make_trial("NCT00000001")

		out, _ = self.run_command()

		trial.refresh_from_db()
		self.assertIsNone(trial.acronym)
		self.assertIn("Not returned by API: 1 NCT ids", out)

	def test_dry_run_saves_nothing(self):
		trial = self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {"NCT00000001": _study("NCT00000001", "OPERA")}

		out, _ = self.run_command(dry_run=True)

		trial.refresh_from_db()
		self.assertIsNone(trial.acronym)
		self.assertIn("Would update 1 trial rows", out)

	def test_batches_requests_by_batch_size(self):
		for i in range(1, 6):
			self.make_trial(f"NCT0000000{i}")

		self.run_command(batch_size=2)

		self.assertEqual([len(c) for c in FakeAPI.calls], [2, 2, 1])

	def test_normalises_nct_case_and_truncates_acronym(self):
		trial = self.make_trial(
			None, identifiers={"nct": " nct00000001 "}, n="NCT00000001"
		)
		FakeAPI.studies_by_nct = {"NCT00000001": _study("NCT00000001", "A" * 250)}

		self.run_command()

		trial.refresh_from_db()
		self.assertEqual(trial.acronym, "A" * 200)
		self.assertEqual(FakeAPI.calls, [["NCT00000001"]])

	def test_invalid_nct_ids_are_skipped(self):
		self.make_trial(None, identifiers={"nct": "not-an-id"}, n="BAD1")

		out, _ = self.run_command()

		self.assertEqual(FakeAPI.calls, [])
		self.assertIn("1 skipped as invalid", out)

	def test_failed_batch_is_retried_then_skipped_and_others_continue(self):
		self.make_trial("NCT00000001")
		self.make_trial("NCT00000002")
		FakeAPI.studies_by_nct = {
			"NCT00000001": _study("NCT00000001", "FIRST"),
			"NCT00000002": _study("NCT00000002", "SECOND"),
		}
		original_search = FakeAPI.search

		def flaky_search(self, filter_ids=None, **kwargs):
			if "NCT00000001" in filter_ids:
				type(self).calls.append(list(filter_ids))
				raise RuntimeError("boom")
			return original_search(self, filter_ids=filter_ids, **kwargs)

		with patch.object(FakeAPI, "search", flaky_search):
			out, err = self.run_command(batch_size=1)

		self.assertEqual(
			Trials.objects.get(identifiers__nct="NCT00000001").acronym, None
		)
		self.assertEqual(
			Trials.objects.get(identifiers__nct="NCT00000002").acronym, "SECOND"
		)
		self.assertIn("failed twice", err)
		self.assertIn("rerun to retry", out)

	def test_limit_caps_candidates(self):
		for i in range(1, 4):
			self.make_trial(f"NCT0000000{i}")
		FakeAPI.studies_by_nct = {
			f"NCT0000000{i}": _study(f"NCT0000000{i}", f"ACR{i}") for i in range(1, 4)
		}

		out, _ = self.run_command(limit=2)

		self.assertIn("2 NCT ids with no acronym", out)
		self.assertIn("Updated 2 trial rows", out)
