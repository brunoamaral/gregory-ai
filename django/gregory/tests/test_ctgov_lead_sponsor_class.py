"""Tests for ClinicalTrials.gov leadSponsor.class capture: extract_sponsor_fields (shared
between parse_study_to_clinical_trial and backfill_trial_sponsors_from_ctgov) and the
live feedreader's create/update write paths.

Run:
	docker exec gregory python manage.py test gregory.tests.test_ctgov_lead_sponsor_class
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import SimpleTestCase, TestCase
from organizations.models import Organization

from gregory.classes import ClinicalTrial, ClinicalTrialsGovAPI
from gregory.management.commands.feedreader_trials_ctgov import Command as CTGovCommand
from gregory.models import Sources, Sponsor, Subject, Team


def _base_study(**overrides):
	study = {
		"protocolSection": {
			"identificationModule": {"nctId": "NCT99999999", "officialTitle": "Test"},
			"statusModule": {"overallStatus": "RECRUITING"},
			"designModule": {},
			"sponsorCollaboratorsModule": {"leadSponsor": {"name": "Lead Corp"}},
			"conditionsModule": {},
			"eligibilityModule": {},
			"outcomesModule": {},
			"contactsLocationsModule": {},
			"descriptionModule": {},
			"armsInterventionsModule": {},
		}
	}
	study["protocolSection"].update(overrides)
	return study


class ExtractSponsorFieldsTests(SimpleTestCase):
	def test_extracts_name_class_and_collaborators(self):
		study = _base_study(
			sponsorCollaboratorsModule={
				"leadSponsor": {"name": "Acme Corp", "class": "INDUSTRY"},
				"collaborators": [{"name": "Collab One"}, {"name": "Collab Two"}],
			}
		)
		fields = ClinicalTrialsGovAPI.extract_sponsor_fields(study)
		self.assertEqual(fields["primary_sponsor"], "Acme Corp")
		self.assertEqual(fields["lead_sponsor_class"], "INDUSTRY")
		self.assertEqual(fields["secondary_sponsor"], "Collab One; Collab Two")

	def test_missing_class_and_collaborators(self):
		study = _base_study(
			sponsorCollaboratorsModule={"leadSponsor": {"name": "Solo Corp"}}
		)
		fields = ClinicalTrialsGovAPI.extract_sponsor_fields(study)
		self.assertEqual(fields["primary_sponsor"], "Solo Corp")
		self.assertIsNone(fields["lead_sponsor_class"])
		self.assertIsNone(fields["secondary_sponsor"])


class ParseStudyLeadSponsorClassTests(SimpleTestCase):
	def test_parse_study_to_clinical_trial_captures_lead_sponsor_class(self):
		api = ClinicalTrialsGovAPI()
		study = _base_study(
			sponsorCollaboratorsModule={
				"leadSponsor": {"name": "Gov Body", "class": "NIH"}
			}
		)
		trial = api.parse_study_to_clinical_trial(study)
		self.assertEqual(trial.extra_fields["lead_sponsor_class"], "NIH")
		self.assertEqual(trial.extra_fields["primary_sponsor"], "Gov Body")


def _ctgov_trial(lead_sponsor_class=None, primary_sponsor="Acme Corp", nct="NCT88888888"):
	extra_fields = {"primary_sponsor": primary_sponsor}
	if lead_sponsor_class is not None:
		extra_fields["lead_sponsor_class"] = lead_sponsor_class
	return ClinicalTrial(
		title="Lead Sponsor Class Trial",
		summary="",
		link=f"https://clinicaltrials.gov/study/{nct}",
		published_date=None,
		identifiers={"nct": nct},
		extra_fields=extra_fields,
	)


def _ctgov_cmd():
	cmd = CTGovCommand()
	cmd.verbosity = 0
	return cmd


class FeedreaderCtgovLeadSponsorClassTests(TestCase):
	"""Confirms the live importer's create/update paths persist lead_sponsor_class end
	to end (the extra_fields plumbing added alongside primary_sponsor), and that it also
	drives sponsor resolution via Trials.save()."""

	def setUp(self):
		org = Organization.objects.create(name="Test Org")
		team = Team.objects.create(organization=org, name="Test Team", slug="test-team")
		subject = Subject.objects.create(subject_name="MS", subject_slug="ms")
		self.source = Sources.objects.create(
			name="CTGov API",
			source_for="trials",
			method="ctgov_api",
			subject=subject,
			team=team,
		)
		self.cmd = _ctgov_cmd()

	def test_create_new_trial_persists_lead_sponsor_class(self):
		trial = self.cmd.create_new_trial(_ctgov_trial("INDUSTRY"), self.source)

		self.assertEqual(trial.lead_sponsor_class, "INDUSTRY")
		self.assertIsInstance(trial.primary_sponsor_normalized, Sponsor)
		self.assertEqual(trial.primary_sponsor_normalized.name, "Acme Corp")

	def test_update_existing_trial_fills_lead_sponsor_class(self):
		trial = self.cmd.create_new_trial(_ctgov_trial(None), self.source)
		self.assertIsNone(trial.lead_sponsor_class)

		self.cmd.update_existing_trial(trial, _ctgov_trial("NIH"), self.source)

		trial.refresh_from_db()
		self.assertEqual(trial.lead_sponsor_class, "NIH")
