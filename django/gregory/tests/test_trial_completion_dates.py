"""Tests for Trials completion date capture (primary_completion_date, completion_date,
completion_date_type): ClinicalTrials.gov parsing, CTIS search + retrieve capture, and
the backfill_trial_completion_dates_from_ctgov command."""

import datetime
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from gregory.classes import ClinicalTrialsGovAPI, CTISPublicAPI
from gregory.management.commands.backfill_trial_completion_dates_from_ctgov import (
	CHANGE_REASON,
)
from gregory.management.commands.feedreader_trials_ctis import (
	Command as CtisCommand,
	_extract_estimated_end_date,
)
from gregory.models import Trials


def _status(**kwargs):
	return kwargs


class ExtractCompletionDatesTest(SimpleTestCase):
	def test_full_date_actual(self):
		primary, completion, kind = ClinicalTrialsGovAPI.extract_completion_dates(
			_status(
				primaryCompletionDateStruct={"date": "2023-05-17", "type": "ACTUAL"},
				completionDateStruct={"date": "2023-09-01", "type": "ACTUAL"},
			)
		)
		self.assertEqual(primary, datetime.date(2023, 5, 17))
		self.assertEqual(completion, datetime.date(2023, 9, 1))
		self.assertEqual(kind, "actual")

	def test_partial_dates_and_estimated(self):
		primary, completion, kind = ClinicalTrialsGovAPI.extract_completion_dates(
			_status(
				primaryCompletionDateStruct={"date": "2027-03", "type": "ESTIMATED"},
				completionDateStruct={"date": "2028", "type": "ESTIMATED"},
			)
		)
		self.assertEqual(primary, datetime.date(2027, 3, 1))
		self.assertEqual(completion, datetime.date(2028, 1, 1))
		self.assertEqual(kind, "estimated")

	def test_overall_struct_decides_type(self):
		_, _, kind = ClinicalTrialsGovAPI.extract_completion_dates(
			_status(
				primaryCompletionDateStruct={"date": "2023-05-17", "type": "ACTUAL"},
				completionDateStruct={"date": "2029-01-01", "type": "ESTIMATED"},
			)
		)
		self.assertEqual(kind, "estimated")

	def test_only_primary_uses_primary_type(self):
		primary, completion, kind = ClinicalTrialsGovAPI.extract_completion_dates(
			_status(primaryCompletionDateStruct={"date": "2023-05-17", "type": "ACTUAL"})
		)
		self.assertEqual(primary, datetime.date(2023, 5, 17))
		self.assertIsNone(completion)
		self.assertEqual(kind, "actual")

	def test_absent_and_unparseable(self):
		self.assertEqual(
			ClinicalTrialsGovAPI.extract_completion_dates({}), (None, None, None)
		)
		self.assertEqual(
			ClinicalTrialsGovAPI.extract_completion_dates(
				_status(completionDateStruct={"date": "soon", "type": "ACTUAL"})
			),
			(None, None, None),
		)

	def test_parse_study_exposes_fields(self):
		study = {
			"protocolSection": {
				"identificationModule": {"nctId": "NCT99999999", "officialTitle": "T"},
				"statusModule": {
					"overallStatus": "COMPLETED",
					"completionDateStruct": {"date": "2022-02-02", "type": "ACTUAL"},
				},
			}
		}
		extras = ClinicalTrialsGovAPI().parse_study_to_clinical_trial(study).extra_fields
		self.assertEqual(extras["completion_date"], datetime.date(2022, 2, 2))
		self.assertEqual(extras["completion_date_type"], "actual")
		self.assertIsNone(extras["primary_completion_date"])


class CtisCompletionDateTest(TestCase):
	def _trial(self, **kwargs):
		return Trials.objects.create(
			title="CTIS trial", link="https://euclinicaltrials.eu/x", identifiers={"euct": "1"}, **kwargs
		)

	@staticmethod
	def _payload(estimated):
		return {
			"authorizedApplication": {
				"authorizedPartI": {
					"trialDetails": {
						"trialInformation": {"trialDuration": {"estimatedEndDate": estimated}}
					}
				}
			}
		}

	def test_search_end_date_is_actual(self):
		record = {"ctNumber": "2026-000000-00-00", "endDate": "31/12/2025"}
		extras = CTISPublicAPI().parse_ctis_search_record(record).extra_fields
		self.assertEqual(extras["completion_date"], datetime.date(2025, 12, 31))
		self.assertEqual(extras["completion_date_type"], "actual")

	def test_search_falls_back_to_end_date_eu(self):
		record = {"ctNumber": "2026-000000-00-00", "endDateEU": "01/02/2026"}
		extras = CTISPublicAPI().parse_ctis_search_record(record).extra_fields
		self.assertEqual(extras["completion_date"], datetime.date(2026, 2, 1))

	def test_search_without_end_date(self):
		extras = CTISPublicAPI().parse_ctis_search_record(
			{"ctNumber": "2026-000000-00-00"}
		).extra_fields
		self.assertNotIn("completion_date", extras)
		self.assertNotIn("completion_date_type", extras)

	def test_extract_estimated_end_date_formats(self):
		self.assertEqual(
			_extract_estimated_end_date(self._payload("2027-06-30")), datetime.date(2027, 6, 30)
		)
		self.assertEqual(
			_extract_estimated_end_date(self._payload("05/07/2027")), datetime.date(2027, 7, 5)
		)
		self.assertIsNone(_extract_estimated_end_date(self._payload("garbage")))
		self.assertIsNone(_extract_estimated_end_date({}))

	def test_retrieve_fills_estimate_when_no_actual(self):
		trial = self._trial()
		self.assertTrue(CtisCommand()._enrich_completion_date(trial, self._payload("2027-06-30")))
		self.assertEqual(trial.completion_date, datetime.date(2027, 6, 30))
		self.assertEqual(trial.completion_date_type, "estimated")

	def test_retrieve_estimate_never_beats_actual(self):
		trial = self._trial(completion_date=datetime.date(2025, 1, 1), completion_date_type="actual")
		self.assertFalse(CtisCommand()._enrich_completion_date(trial, self._payload("2027-06-30")))
		self.assertEqual(trial.completion_date, datetime.date(2025, 1, 1))
		self.assertEqual(trial.completion_date_type, "actual")

	def test_retrieve_refreshes_estimate_and_is_idempotent(self):
		trial = self._trial(completion_date=datetime.date(2026, 1, 1), completion_date_type="estimated")
		cmd = CtisCommand()
		self.assertTrue(cmd._enrich_completion_date(trial, self._payload("2027-06-30")))
		self.assertFalse(cmd._enrich_completion_date(trial, self._payload("2027-06-30")))


def _study(nct, primary=None, completion=None, kind="ACTUAL"):
	status = {}
	if primary:
		status["primaryCompletionDateStruct"] = {"date": primary, "type": kind}
	if completion:
		status["completionDateStruct"] = {"date": completion, "type": kind}
	return {
		"protocolSection": {
			"identificationModule": {"nctId": nct},
			"statusModule": status,
		}
	}


class FakeAPI:
	studies_by_nct = {}

	def __init__(self):
		pass

	def search(self, filter_ids=None, **kwargs):
		return {"studies": [self.studies_by_nct[n] for n in filter_ids if n in self.studies_by_nct]}


@patch(
	"gregory.management.commands.backfill_trial_completion_dates_from_ctgov.ClinicalTrialsGovAPI",
	FakeAPI,
)
class BackfillCompletionDatesTest(TestCase):
	def setUp(self):
		FakeAPI.studies_by_nct = {}

	def run_command(self, **kwargs):
		out = StringIO()
		call_command(
			"backfill_trial_completion_dates_from_ctgov", sleep=0, stdout=out, stderr=StringIO(), **kwargs
		)
		return out.getvalue()

	def make_trial(self, nct, **kwargs):
		return Trials.objects.create(
			title=f"Trial {nct}",
			link=f"https://clinicaltrials.gov/study/{nct}",
			identifiers={"nct": nct},
			**kwargs,
		)

	def test_fills_dates_and_records_change_reason(self):
		trial = self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {"NCT00000001": _study("NCT00000001", "2020-01-02", "2020-06")}

		self.run_command()

		trial.refresh_from_db()
		self.assertEqual(trial.primary_completion_date, datetime.date(2020, 1, 2))
		self.assertEqual(trial.completion_date, datetime.date(2020, 6, 1))
		self.assertEqual(trial.completion_date_type, "actual")
		self.assertEqual(trial.history.first().history_change_reason, CHANGE_REASON)

	def test_skips_trials_that_already_have_dates(self):
		trial = self.make_trial("NCT00000002", completion_date=datetime.date(2019, 1, 1))
		FakeAPI.studies_by_nct = {"NCT00000002": _study("NCT00000002", "2030-01-01", "2030-02-01")}

		out = self.run_command()

		trial.refresh_from_db()
		self.assertEqual(trial.completion_date, datetime.date(2019, 1, 1))
		self.assertIn("0 NCT ids", out)

	def test_dry_run_writes_nothing(self):
		trial = self.make_trial("NCT00000003")
		FakeAPI.studies_by_nct = {"NCT00000003": _study("NCT00000003", "2020-01-02")}

		out = self.run_command(dry_run=True)

		trial.refresh_from_db()
		self.assertIsNone(trial.primary_completion_date)
		self.assertIn("Would fill", out)

	def test_registry_without_dates_leaves_trial_untouched(self):
		trial = self.make_trial("NCT00000004")
		FakeAPI.studies_by_nct = {"NCT00000004": _study("NCT00000004")}

		self.run_command()

		trial.refresh_from_db()
		self.assertIsNone(trial.completion_date)
