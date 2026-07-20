"""Tests for feedreader_trials_ctgov's TrialSite capture (TRIAL-GEOGRAPHY-PLAN.md PR
G2): contactsLocationsModule.locations[] -> TrialSite, source-scoped replace, on both
the create and update paths. No extra API call — the study JSON is already fetched.

Run:
  docker exec gregory python manage.py test gregory.tests.management.test_feedreader_trials_ctgov_sites
"""

import os
from unittest.mock import MagicMock

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import TestCase

from gregory.classes import ClinicalTrial, ClinicalTrialsGovAPI
from gregory.management.commands.feedreader_trials_ctgov import Command
from gregory.models import Sources, Trials


def _study(nct_id, locations=None):
	protocol = {"identificationModule": {"nctId": nct_id}}
	if locations is not None:
		protocol["contactsLocationsModule"] = {"locations": locations}
	return {"protocolSection": protocol}


def _clinical_trial(nct="NCT00000001", title="Test trial title"):
	return ClinicalTrial(
		title=title,
		summary="A summary",
		link=f"https://clinicaltrials.gov/study/{nct}",
		published_date=None,
		identifiers={"nct": nct},
		extra_fields={},
	)


def make_command():
	cmd = Command()
	cmd.debug = False
	# Real extract_sites logic, but search_all/parse_study_to_clinical_trial are
	# driven per-test so no network call is made.
	cmd.api = MagicMock()
	cmd.api.extract_sites = ClinicalTrialsGovAPI.extract_sites
	return cmd


class CtgovSiteCaptureTests(TestCase):
	def setUp(self):
		self.source = Sources.objects.create(
			name="CTGov Test",
			method="ctgov_api",
			source_for="trials",
			active=True,
			ctgov_search_condition="multiple sclerosis",
		)

	def test_created_trial_gets_sites(self):
		study = _study(
			"NCT00000001",
			locations=[{"facility": "Site A", "city": "Lisbon", "country": "Portugal"}],
		)
		cmd = make_command()
		cmd.api.search_all.return_value = iter([study])
		cmd.api.parse_study_to_clinical_trial.return_value = _clinical_trial()

		cmd.process_sources(max_results=10)

		trial = Trials.objects.get(identifiers__nct="NCT00000001")
		sites = list(trial.trial_sites.all())
		self.assertEqual(len(sites), 1)
		self.assertEqual(sites[0].name, "Site A")
		self.assertEqual(sites[0].sources, ["ctgov"])

	def test_updated_trial_gets_sites(self):
		existing = Trials.objects.create(
			title="Existing trial",
			link="https://clinicaltrials.gov/study/NCT00000002",
			identifiers={"nct": "NCT00000002"},
		)
		study = _study(
			"NCT00000002",
			locations=[{"facility": "Site B", "city": "Porto", "country": "Portugal"}],
		)
		cmd = make_command()
		cmd.api.search_all.return_value = iter([study])
		cmd.api.parse_study_to_clinical_trial.return_value = _clinical_trial(
			nct="NCT00000002", title="Existing trial"
		)

		cmd.process_sources(max_results=10)

		existing.refresh_from_db()
		sites = list(existing.trial_sites.all())
		self.assertEqual(len(sites), 1)
		self.assertEqual(sites[0].name, "Site B")

	def test_rerun_replaces_only_ctgov_sites_not_ctis(self):
		existing = Trials.objects.create(
			title="Existing trial",
			link="https://clinicaltrials.gov/study/NCT00000003",
			identifiers={"nct": "NCT00000003"},
		)
		from gregory.utils.trial_site_sync import replace_trial_sites

		replace_trial_sites(existing, "ctis", [{"name": "CTIS Hospital", "city": "Rome"}])

		study = _study(
			"NCT00000003",
			locations=[{"facility": "CTGov Site", "city": "Porto", "country": "Portugal"}],
		)
		cmd = make_command()
		cmd.api.search_all.return_value = iter([study])
		cmd.api.parse_study_to_clinical_trial.return_value = _clinical_trial(
			nct="NCT00000003", title="Existing trial"
		)

		cmd.process_sources(max_results=10)

		sites = list(existing.trial_sites.all())
		self.assertEqual(len(sites), 2)
		by_source = {tuple(s.sources): s.name for s in sites}
		self.assertEqual(by_source[("ctis",)], "CTIS Hospital")
		self.assertEqual(by_source[("ctgov",)], "CTGov Site")

	def test_no_locations_creates_zero_sites_without_error(self):
		study = _study("NCT00000004", locations=[])
		cmd = make_command()
		cmd.api.search_all.return_value = iter([study])
		cmd.api.parse_study_to_clinical_trial.return_value = _clinical_trial(
			nct="NCT00000004"
		)

		cmd.process_sources(max_results=10)

		trial = Trials.objects.get(identifiers__nct="NCT00000004")
		self.assertEqual(trial.trial_sites.count(), 0)

	def test_site_capture_failure_does_not_block_trial_creation(self):
		study = _study("NCT00000005", locations=[{"facility": "Site X", "city": "Faro"}])
		cmd = make_command()
		cmd.api.search_all.return_value = iter([study])
		cmd.api.parse_study_to_clinical_trial.return_value = _clinical_trial(
			nct="NCT00000005"
		)
		cmd.api.extract_sites = MagicMock(side_effect=Exception("boom"))

		cmd.process_sources(max_results=10)

		self.assertTrue(Trials.objects.filter(identifiers__nct="NCT00000005").exists())
