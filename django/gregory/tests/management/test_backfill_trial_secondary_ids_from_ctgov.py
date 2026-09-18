"""Tests for the backfill_trial_secondary_ids_from_ctgov management command.

Mirrors gregory/tests/management/test_backfill_trial_sponsors_from_ctgov.py's
structure and FakeAPI pattern — no network calls.

Run:
	docker exec gregory python manage.py test gregory.tests.management.test_backfill_trial_secondary_ids_from_ctgov
"""

import os
from io import StringIO
from unittest.mock import patch

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.core.management import call_command
from django.test import TestCase

from gregory.management.commands.backfill_trial_secondary_ids_from_ctgov import (
	CHANGE_REASON,
	FIELDS,
)
from gregory.models import Trials


def _study(nct_id, secondary_id_infos=None):
	identification = {"nctId": nct_id}
	if secondary_id_infos is not None:
		identification["secondaryIdInfos"] = secondary_id_infos
	return {"protocolSection": {"identificationModule": identification}}


class FakeAPI:
	"""Stand-in for ClinicalTrialsGovAPI returning canned studies (no network)."""

	studies_by_nct = {}
	calls = []
	fields_seen = []
	fail_next_n_calls = 0

	def __init__(self):
		pass

	def search(self, filter_ids=None, fields=None, **kwargs):
		type(self).calls.append(list(filter_ids))
		type(self).fields_seen.append(fields)
		if type(self).fail_next_n_calls > 0:
			type(self).fail_next_n_calls -= 1
			raise ConnectionError("simulated transient failure")
		return {
			"studies": [
				self.studies_by_nct[nct]
				for nct in filter_ids
				if nct in self.studies_by_nct
			]
		}


@patch(
	"gregory.management.commands.backfill_trial_secondary_ids_from_ctgov.ClinicalTrialsGovAPI",
	FakeAPI,
)
class BackfillTrialSecondaryIdsFromCtgovTest(TestCase):
	def setUp(self):
		FakeAPI.studies_by_nct = {}
		FakeAPI.calls = []
		FakeAPI.fields_seen = []
		FakeAPI.fail_next_n_calls = 0

	def run_command(self, **kwargs):
		out, err = StringIO(), StringIO()
		call_command(
			"backfill_trial_secondary_ids_from_ctgov",
			sleep=0,
			stdout=out,
			stderr=err,
			**kwargs,
		)
		return out.getvalue(), err.getvalue()

	def make_trial(self, nct, n=None, **extra):
		n = n if n is not None else (nct or "X")
		return Trials.objects.create(
			title=f"Trial {n}",
			link=f"https://clinicaltrials.gov/study/{n}",
			identifiers={"nct": nct} if nct else {},
			**extra,
		)

	def test_requests_expected_ctgov_fields(self):
		self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {"NCT00000001": _study("NCT00000001")}

		self.run_command()

		self.assertEqual(FakeAPI.fields_seen, [FIELDS])
		self.assertIn("protocolSection.identificationModule.nctId", FIELDS)
		self.assertIn("protocolSection.identificationModule.secondaryIdInfos", FIELDS)

	def test_fills_null_ctg_secondary_ids_and_recomputes_identifiers_normalized(self):
		trial = self.make_trial("NCT00000001")
		self.assertIsNone(trial.ctg_secondary_ids)
		FakeAPI.studies_by_nct = {
			"NCT00000001": _study(
				"NCT00000001",
				[{"id": "2020-001234-12", "type": "EUDRACT_NUMBER", "domain": "", "link": ""}],
			)
		}

		out, _ = self.run_command()

		trial.refresh_from_db()
		self.assertEqual(
			trial.ctg_secondary_ids,
			[{"id": "2020-001234-12", "type": "EUDRACT_NUMBER", "domain": "", "link": ""}],
		)
		self.assertEqual(
			trial.identifiers_normalized,
			["eudract:2020-001234-12", "nct:NCT00000001"],
		)
		self.assertEqual(trial.history.first().history_change_reason, CHANGE_REASON)
		self.assertIn("Filled ctg_secondary_ids on 1 trial row(s)", out)

	def test_writes_empty_list_when_study_lists_no_secondary_ids(self):
		trial = self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {"NCT00000001": _study("NCT00000001")}  # no secondaryIdInfos key

		self.run_command()

		trial.refresh_from_db()
		self.assertEqual(trial.ctg_secondary_ids, [])
		# identifiers_normalized is still recomputed from the other (unchanged) inputs.
		self.assertEqual(trial.identifiers_normalized, ["nct:NCT00000001"])

	def test_selection_excludes_trials_already_fetched(self):
		# ctg_secondary_ids already [] (fetched, none found) -> not NULL -> not selected.
		already_fetched = self.make_trial("NCT00000001", ctg_secondary_ids=[])
		FakeAPI.studies_by_nct = {
			"NCT00000001": _study(
				"NCT00000001", [{"id": "NEW", "type": "OTHER", "domain": "", "link": ""}]
			)
		}

		out, _ = self.run_command()

		already_fetched.refresh_from_db()
		self.assertEqual(FakeAPI.calls, [])
		self.assertEqual(already_fetched.ctg_secondary_ids, [])
		self.assertIn("0 NCT ids missing ctg_secondary_ids", out)

	def test_idempotent_on_second_run(self):
		trial = self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {
			"NCT00000001": _study(
				"NCT00000001", [{"id": "ISRCTN14048364", "type": "OTHER", "domain": "", "link": ""}]
			)
		}

		self.run_command()
		trial.refresh_from_db()
		first_result = trial.ctg_secondary_ids

		out2, _ = self.run_command()

		self.assertIn("0 NCT ids missing ctg_secondary_ids", out2)
		trial.refresh_from_db()
		self.assertEqual(trial.ctg_secondary_ids, first_result)

	def test_dry_run_saves_nothing(self):
		trial = self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {
			"NCT00000001": _study(
				"NCT00000001", [{"id": "ISRCTN14048364", "type": "OTHER", "domain": "", "link": ""}]
			)
		}

		out, _ = self.run_command(dry_run=True)

		trial.refresh_from_db()
		self.assertIsNone(trial.ctg_secondary_ids)
		# identifiers_normalized was already ["nct:NCT00000001"] from creation
		# (Trials.save() always recomputes it from whatever's actually stored) —
		# a dry run must leave it exactly as-is, not pull in the fetched-but-
		# unsaved ISRCTN id.
		self.assertEqual(trial.identifiers_normalized, ["nct:NCT00000001"])
		self.assertIn("Would fill ctg_secondary_ids on 1 trial row(s)", out)

	def test_limit_caps_candidates(self):
		for i in range(1, 4):
			self.make_trial(f"NCT0000000{i}")
		FakeAPI.studies_by_nct = {
			f"NCT0000000{i}": _study(f"NCT0000000{i}") for i in range(1, 4)
		}

		out, _ = self.run_command(limit=2)

		self.assertIn("2 NCT ids missing ctg_secondary_ids", out)

	def test_invalid_nct_ids_are_skipped(self):
		self.make_trial("not-an-id", n="BAD1")

		out, _ = self.run_command()

		self.assertEqual(FakeAPI.calls, [])
		self.assertIn("1 skipped as invalid", out)

	def test_not_found_ncts_stay_null_and_are_reported(self):
		self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {}  # API returns nothing for this NCT

		out, _ = self.run_command()

		trial = Trials.objects.get(identifiers__nct="NCT00000001")
		self.assertIsNone(trial.ctg_secondary_ids)
		self.assertIn("Not found on ClinicalTrials.gov: 1 NCT ids", out)
		self.assertIn("NCT00000001", out)

	def test_failed_batch_is_retried_then_reported(self):
		self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {"NCT00000001": _study("NCT00000001")}
		FakeAPI.fail_next_n_calls = 3  # exhausts all 3 attempts for the one batch

		out, err = self.run_command()

		# 3 attempts logged to stderr, then given up on and reported on stdout.
		self.assertEqual(len(FakeAPI.calls), 3)
		self.assertIn("failed", err.lower())
		self.assertIn("batch(es) failed and were skipped", out)
		trial = Trials.objects.get(identifiers__nct="NCT00000001")
		self.assertIsNone(trial.ctg_secondary_ids)

	def test_failed_batch_recovers_after_retry(self):
		self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {"NCT00000001": _study("NCT00000001")}
		FakeAPI.fail_next_n_calls = 2  # fails twice, succeeds on the 3rd attempt

		out, _ = self.run_command()

		self.assertEqual(len(FakeAPI.calls), 3)
		trial = Trials.objects.get(identifiers__nct="NCT00000001")
		self.assertEqual(trial.ctg_secondary_ids, [])
		self.assertIn("Filled ctg_secondary_ids on 1 trial row(s)", out)
