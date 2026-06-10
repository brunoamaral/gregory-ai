"""
Tests for acronym capture in the ClinicalTrials.gov ingestion path.

Covers:
  - parse_study_to_clinical_trial extracts, strips, and truncates the acronym
  - create_new_trial stores the acronym on new trials
  - update_existing_trial fills an empty acronym but never replaces one set
    by an earlier import (e.g. WHO ICTRP) — fill-once, like links

Run:
  docker exec gregory python manage.py test gregory.tests.test_ctgov_acronym
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.test import SimpleTestCase, TestCase
from organizations.models import Organization

from gregory.models import Trials, Sources, Subject, Team
from gregory.classes import ClinicalTrial, ClinicalTrialsGovAPI
from gregory.management.commands.feedreader_trials_ctgov import Command as CTGovCommand


def _study(acronym=None):
	identification = {
		'nctId': 'NCT00000001',
		'officialTitle': 'A Study of Acronym Capture',
	}
	if acronym is not None:
		identification['acronym'] = acronym
	return {'protocolSection': {'identificationModule': identification}}


def _ctgov_trial(acronym=None):
	return ClinicalTrial(
		title='A Study of Acronym Capture',
		summary='Summary from ClinicalTrials.gov',
		link='https://clinicaltrials.gov/study/NCT00000001',
		published_date=None,
		identifiers={'nct': 'NCT00000001'},
		extra_fields={'acronym': acronym} if acronym else {},
	)


def _ctgov_cmd():
	cmd = CTGovCommand()
	cmd.verbosity = 0
	return cmd


class ParseStudyAcronymTest(SimpleTestCase):
	def setUp(self):
		self.api = ClinicalTrialsGovAPI()

	def test_extracts_and_strips_acronym(self):
		trial = self.api.parse_study_to_clinical_trial(_study('  OPERA '))
		self.assertEqual(trial.extra_fields['acronym'], 'OPERA')

	def test_missing_acronym_is_none(self):
		trial = self.api.parse_study_to_clinical_trial(_study())
		self.assertIsNone(trial.extra_fields['acronym'])

	def test_blank_acronym_is_none(self):
		trial = self.api.parse_study_to_clinical_trial(_study('   '))
		self.assertIsNone(trial.extra_fields['acronym'])

	def test_long_acronym_is_truncated_to_field_limit(self):
		trial = self.api.parse_study_to_clinical_trial(_study('A' * 250))
		self.assertEqual(trial.extra_fields['acronym'], 'A' * 200)
		self.assertEqual(len(trial.extra_fields['acronym']), Trials._meta.get_field('acronym').max_length)


class ImporterAcronymTest(TestCase):
	def setUp(self):
		org = Organization.objects.create(name='Test Org')
		team = Team.objects.create(organization=org, name='Test Team', slug='test-team')
		subject = Subject.objects.create(subject_name='MS', subject_slug='ms')
		self.source = Sources.objects.create(
			name='CTGov API',
			source_for='trials',
			method='ctgov_api',
			subject=subject,
			team=team,
		)
		self.cmd = _ctgov_cmd()

	def test_create_new_trial_stores_acronym(self):
		self.cmd.create_new_trial(_ctgov_trial('OPERA'), self.source)
		self.assertEqual(Trials.objects.get(identifiers__nct='NCT00000001').acronym, 'OPERA')

	def test_update_fills_empty_acronym(self):
		trial = self.cmd.create_new_trial(_ctgov_trial(), self.source)
		self.assertIsNone(trial.acronym)

		self.cmd.update_existing_trial(trial, _ctgov_trial('OPERA'), self.source)

		trial.refresh_from_db()
		self.assertEqual(trial.acronym, 'OPERA')

	def test_update_never_replaces_existing_acronym(self):
		trial = self.cmd.create_new_trial(_ctgov_trial('FROM-WHO'), self.source)

		self.cmd.update_existing_trial(trial, _ctgov_trial('FROM-CTGOV'), self.source)

		trial.refresh_from_db()
		self.assertEqual(trial.acronym, 'FROM-WHO')

	def test_update_without_acronym_leaves_field_alone(self):
		trial = self.cmd.create_new_trial(_ctgov_trial('OPERA'), self.source)

		self.cmd.update_existing_trial(trial, _ctgov_trial(), self.source)

		trial.refresh_from_db()
		self.assertEqual(trial.acronym, 'OPERA')
