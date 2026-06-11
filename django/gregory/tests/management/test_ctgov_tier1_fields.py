"""
Command-level tests for Tier-1 field gap fixes in the CTGov importer.

Verifies that new fields persist on create and that the non-destructive
update guard applies: an existing non-empty value is replaced by a new
non-empty value, but never blanked by an empty/None incoming one.

Run:
  docker exec gregory python manage.py test gregory.tests.management.test_ctgov_tier1_fields
"""
import datetime
import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.test import TestCase
from organizations.models import Organization

from gregory.classes import ClinicalTrial
from gregory.management.commands.feedreader_trials_ctgov import Command as CTGovCommand
from gregory.models import Sources, Subject, Team, Trials


def _source():
	org = Organization.objects.create(name='Test Org Tier1')
	team = Team.objects.create(organization=org, name='Test Team Tier1', slug='test-team-tier1')
	subject = Subject.objects.create(subject_name='MS Tier1', subject_slug='ms-tier1')
	return Sources.objects.create(
		name='CTGov Tier1',
		source_for='trials',
		method='ctgov_api',
		subject=subject,
		team=team,
	)


def _trial(nct='NCT88880001', **extra):
	return ClinicalTrial(
		title='Tier-1 Command Test Trial',
		summary='Summary text.',
		link=f'https://clinicaltrials.gov/study/{nct}',
		published_date=None,
		identifiers={'nct': nct},
		extra_fields=extra,
	)


def _cmd():
	cmd = CTGovCommand()
	cmd.verbosity = 0
	return cmd


class Tier1CreateTest(TestCase):
	def setUp(self):
		self.source = _source()
		self.cmd = _cmd()

	def test_create_stores_study_design(self):
		self.cmd.create_new_trial(_trial(study_design='Allocation: RANDOMIZED'), self.source)
		t = Trials.objects.get(identifiers__nct='NCT88880001')
		self.assertEqual(t.study_design, 'Allocation: RANDOMIZED')

	def test_create_stores_results_ipd_fields(self):
		self.cmd.create_new_trial(_trial(
			nct='NCT88880002',
			results_ipd_plan='YES',
			results_ipd_description='After 2 years',
		), self.source)
		t = Trials.objects.get(identifiers__nct='NCT88880002')
		self.assertEqual(t.results_ipd_plan, 'YES')
		self.assertEqual(t.results_ipd_description, 'After 2 years')

	def test_create_stores_secondary_sponsor(self):
		self.cmd.create_new_trial(_trial(nct='NCT88880003', secondary_sponsor='Org A; Org B'), self.source)
		t = Trials.objects.get(identifiers__nct='NCT88880003')
		self.assertEqual(t.secondary_sponsor, 'Org A; Org B')

	def test_create_stores_last_refreshed_on(self):
		d = datetime.date(2024, 5, 10)
		self.cmd.create_new_trial(_trial(nct='NCT88880004', last_refreshed_on=d), self.source)
		t = Trials.objects.get(identifiers__nct='NCT88880004')
		self.assertEqual(t.last_refreshed_on, d)

	def test_create_stores_date_enrollement(self):
		d = datetime.date(2023, 1, 1)
		self.cmd.create_new_trial(_trial(nct='NCT88880005', date_enrollement=d), self.source)
		t = Trials.objects.get(identifiers__nct='NCT88880005')
		self.assertEqual(t.date_enrollement, d)

	def test_create_stores_contact_affiliation(self):
		self.cmd.create_new_trial(_trial(nct='NCT88880006', contact_affiliation='University Hospital'), self.source)
		t = Trials.objects.get(identifiers__nct='NCT88880006')
		self.assertEqual(t.contact_affiliation, 'University Hospital')


class Tier1UpdateGuardTest(TestCase):
	def setUp(self):
		self.source = _source()
		self.cmd = _cmd()

	def _create(self, nct, **extra):
		return self.cmd.create_new_trial(_trial(nct=nct, **extra), self.source)

	def test_update_replaces_study_design_with_new_value(self):
		t = self._create('NCT88881001', study_design='Old design')
		self.cmd.update_existing_trial(t, _trial(nct='NCT88881001', study_design='New design'), self.source)
		t.refresh_from_db()
		self.assertEqual(t.study_design, 'New design')

	def test_update_does_not_blank_study_design(self):
		t = self._create('NCT88881002', study_design='Existing design')
		self.cmd.update_existing_trial(t, _trial(nct='NCT88881002'), self.source)
		t.refresh_from_db()
		self.assertEqual(t.study_design, 'Existing design')

	def test_update_replaces_secondary_sponsor(self):
		t = self._create('NCT88881003', secondary_sponsor='Old Org')
		self.cmd.update_existing_trial(t, _trial(nct='NCT88881003', secondary_sponsor='New Org'), self.source)
		t.refresh_from_db()
		self.assertEqual(t.secondary_sponsor, 'New Org')

	def test_update_does_not_blank_secondary_sponsor(self):
		t = self._create('NCT88881004', secondary_sponsor='Preserved Org')
		self.cmd.update_existing_trial(t, _trial(nct='NCT88881004', secondary_sponsor=None), self.source)
		t.refresh_from_db()
		self.assertEqual(t.secondary_sponsor, 'Preserved Org')

	def test_update_replaces_contact_affiliation(self):
		t = self._create('NCT88881005', contact_affiliation='Old Hospital')
		self.cmd.update_existing_trial(t, _trial(nct='NCT88881005', contact_affiliation='New Hospital'), self.source)
		t.refresh_from_db()
		self.assertEqual(t.contact_affiliation, 'New Hospital')

	def test_update_does_not_blank_contact_affiliation(self):
		t = self._create('NCT88881006', contact_affiliation='Existing Hospital')
		self.cmd.update_existing_trial(t, _trial(nct='NCT88881006'), self.source)
		t.refresh_from_db()
		self.assertEqual(t.contact_affiliation, 'Existing Hospital')

	def test_update_replaces_last_refreshed_on(self):
		old_date = datetime.date(2023, 1, 1)
		new_date = datetime.date(2024, 6, 15)
		t = self._create('NCT88881007', last_refreshed_on=old_date)
		self.cmd.update_existing_trial(t, _trial(nct='NCT88881007', last_refreshed_on=new_date), self.source)
		t.refresh_from_db()
		self.assertEqual(t.last_refreshed_on, new_date)

	def test_update_does_not_blank_last_refreshed_on(self):
		d = datetime.date(2024, 1, 1)
		t = self._create('NCT88881008', last_refreshed_on=d)
		self.cmd.update_existing_trial(t, _trial(nct='NCT88881008', last_refreshed_on=None), self.source)
		t.refresh_from_db()
		self.assertEqual(t.last_refreshed_on, d)
