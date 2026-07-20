"""Tests for the backfill_trial_sites_from_ctgov management command
(TRIAL-GEOGRAPHY-PLAN.md PR G2)."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from gregory.management.commands.backfill_trial_sites_from_ctgov import FIELDS
from gregory.models import Trials
from gregory.utils.trial_site_sync import replace_trial_sites


def _study(nct_id, locations=None):
	protocol = {"identificationModule": {"nctId": nct_id}}
	if locations is not None:
		protocol["contactsLocationsModule"] = {"locations": locations}
	return {"protocolSection": protocol}


class FakeAPI:
	"""Stand-in for ClinicalTrialsGovAPI returning canned studies (no network)."""

	studies_by_nct = {}
	calls = []
	fields_seen = []

	def __init__(self):
		pass

	def search(self, filter_ids=None, fields=None, **kwargs):
		type(self).calls.append(list(filter_ids))
		type(self).fields_seen.append(fields)
		return {
			"studies": [
				self.studies_by_nct[nct]
				for nct in filter_ids
				if nct in self.studies_by_nct
			]
		}


@patch(
	"gregory.management.commands.backfill_trial_sites_from_ctgov.ClinicalTrialsGovAPI",
	FakeAPI,
)
class BackfillTrialSitesFromCtgovTest(TestCase):
	def setUp(self):
		FakeAPI.studies_by_nct = {}
		FakeAPI.calls = []
		FakeAPI.fields_seen = []

	def run_command(self, **kwargs):
		out, err = StringIO(), StringIO()
		call_command(
			"backfill_trial_sites_from_ctgov", sleep=0, stdout=out, stderr=err, **kwargs
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

	def test_creates_sites_for_candidate_trial(self):
		trial = self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {
			"NCT00000001": _study(
				"NCT00000001",
				[{"facility": "Site A", "city": "Lisbon", "country": "Portugal"}],
			)
		}

		out, _ = self.run_command()

		sites = list(trial.trial_sites.all())
		self.assertEqual(len(sites), 1)
		self.assertEqual(sites[0].name, "Site A")
		self.assertEqual(sites[0].sources, ["ctgov"])
		self.assertIn("Created 1 TrialSite rows across 1 trials", out)

	def test_requests_expected_ctgov_fields(self):
		self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {
			"NCT00000001": _study("NCT00000001", [{"facility": "Site A", "city": "X"}])
		}

		self.run_command()

		self.assertEqual(FakeAPI.fields_seen, [FIELDS])
		self.assertIn("protocolSection.identificationModule.nctId", FIELDS)
		self.assertIn("protocolSection.contactsLocationsModule", FIELDS)

	def test_selection_skips_trial_that_already_has_ctgov_sites(self):
		trial = self.make_trial("NCT00000001")
		replace_trial_sites(trial, "ctgov", [{"name": "Existing Site", "city": "Faro"}])
		FakeAPI.studies_by_nct = {
			"NCT00000001": _study(
				"NCT00000001", [{"facility": "New Site", "city": "Lisbon"}]
			)
		}

		out, _ = self.run_command()

		self.assertEqual(FakeAPI.calls, [])
		self.assertIn("0 NCT ids with no ctgov-sourced sites", out)
		sites = list(trial.trial_sites.all())
		self.assertEqual(len(sites), 1)
		self.assertEqual(sites[0].name, "Existing Site")

	def test_selection_does_not_skip_trial_with_only_ctis_sites(self):
		"""A trial with ctis sites but no ctgov sites yet is still a candidate —
		selection is scoped to the ctgov source specifically."""
		trial = self.make_trial("NCT00000001")
		replace_trial_sites(trial, "ctis", [{"name": "CTIS Hospital", "city": "Rome"}])
		FakeAPI.studies_by_nct = {
			"NCT00000001": _study(
				"NCT00000001", [{"facility": "CTGov Site", "city": "Lisbon"}]
			)
		}

		self.run_command()

		sites = list(trial.trial_sites.all())
		self.assertEqual(len(sites), 2)
		by_source = {tuple(s.sources): s.name for s in sites}
		self.assertEqual(by_source[("ctis",)], "CTIS Hospital")
		self.assertEqual(by_source[("ctgov",)], "CTGov Site")

	def test_idempotent_second_run_creates_nothing(self):
		trial = self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {
			"NCT00000001": _study(
				"NCT00000001", [{"facility": "Site A", "city": "Lisbon"}]
			)
		}

		self.run_command()
		FakeAPI.calls = []
		out, _ = self.run_command()

		self.assertEqual(FakeAPI.calls, [])
		self.assertIn("0 NCT ids with no ctgov-sourced sites", out)
		self.assertEqual(trial.trial_sites.count(), 1)

	def test_dry_run_writes_nothing(self):
		trial = self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {
			"NCT00000001": _study(
				"NCT00000001", [{"facility": "Site A", "city": "Lisbon"}]
			)
		}

		out, _ = self.run_command(dry_run=True)

		self.assertEqual(trial.trial_sites.count(), 0)
		self.assertIn("Would create 1 TrialSite rows", out)

	def test_no_locations_case_is_counted(self):
		self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {"NCT00000001": _study("NCT00000001", [])}

		out, _ = self.run_command()

		self.assertIn("No locations on registry: 1 trials", out)

	def test_never_calls_trial_save(self):
		"""Sites don't affect any derived trial field, so the backfill must write
		only via replace_trial_sites — never Trials.save() (which would otherwise
		re-run sync_trial_countries ~12.8k times for no reason)."""
		self.make_trial("NCT00000001")
		FakeAPI.studies_by_nct = {
			"NCT00000001": _study(
				"NCT00000001", [{"facility": "Site A", "city": "Lisbon"}]
			)
		}

		with patch.object(Trials, "save") as mock_save:
			self.run_command()

		mock_save.assert_not_called()

	def test_limit_caps_candidates(self):
		for i in range(1, 4):
			self.make_trial(f"NCT0000000{i}")
		FakeAPI.studies_by_nct = {
			f"NCT0000000{i}": _study(
				f"NCT0000000{i}", [{"facility": "Site", "city": "Lisbon"}]
			)
			for i in range(1, 4)
		}

		out, _ = self.run_command(limit=2)

		self.assertIn("2 NCT ids with no ctgov-sourced sites", out)
