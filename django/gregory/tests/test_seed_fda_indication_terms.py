"""Tests for the data migration that seeds Subject.fda_indication_terms from the EMA
areas. The migration's function runs against subjects built here, with the real app registry."""

from importlib import import_module

from django.apps import apps
from django.test import TestCase
from organizations.models import Organization

from gregory.models import Subject, Team

seed_terms = import_module("gregory.migrations.0130_seed_fda_indication_terms").seed_terms


class SeedFdaIndicationTermsTests(TestCase):
	@classmethod
	def setUpTestData(cls):
		org = Organization.objects.create(name="Seed Org", slug="seed-org")
		cls.team = Team.objects.create(organization=org, name="Seed Team", slug="seed-team")

	def subject(self, name, areas, terms=()):
		return Subject.objects.create(
			subject_name=name,
			subject_slug=name.lower().replace(" ", "-"),
			team=self.team,
			ema_therapeutic_areas=list(areas),
			fda_indication_terms=list(terms),
		)

	def terms(self, subject):
		return Subject.objects.get(pk=subject.pk).fda_indication_terms

	def test_each_known_area_gets_its_term(self):
		ms = self.subject("MS", ["Multiple Sclerosis"])
		alzheimers = self.subject("Alzheimers", ["Alzheimer Disease", "Dementia"])
		parkinsons = self.subject("Parkinsons", ["Parkinson Disease"])
		seed_terms(apps, None)
		self.assertEqual(self.terms(ms), ["multiple sclerosis"])
		self.assertEqual(self.terms(alzheimers), ["alzheimer", "dementia"])
		self.assertEqual(self.terms(parkinsons), ["parkinson"])

	def test_areas_are_compared_ignoring_case_and_spacing(self):
		ms = self.subject("MS", ["  multiple   SCLEROSIS "])
		seed_terms(apps, None)
		self.assertEqual(self.terms(ms), ["multiple sclerosis"])

	def test_unknown_areas_are_skipped(self):
		subject = self.subject("Mixed", ["Diabetes Mellitus", "Parkinson Disease"])
		seed_terms(apps, None)
		self.assertEqual(self.terms(subject), ["parkinson"])

	def test_a_subject_with_only_unknown_areas_stays_empty(self):
		subject = self.subject("Diabetes", ["Diabetes Mellitus"])
		seed_terms(apps, None)
		self.assertEqual(self.terms(subject), [])

	def test_a_subject_with_terms_already_set_is_not_touched(self):
		subject = self.subject("MS", ["Multiple Sclerosis"], terms=["relapsing"])
		seed_terms(apps, None)
		self.assertEqual(self.terms(subject), ["relapsing"])

	def test_a_subject_with_no_areas_stays_empty(self):
		subject = self.subject("Research", [])
		seed_terms(apps, None)
		self.assertEqual(self.terms(subject), [])

	def test_duplicate_areas_give_one_term(self):
		subject = self.subject("Dup", ["Dementia", "dementia"])
		seed_terms(apps, None)
		self.assertEqual(self.terms(subject), ["dementia"])

	def test_running_it_twice_changes_nothing(self):
		subject = self.subject("MS", ["Multiple Sclerosis"])
		seed_terms(apps, None)
		seed_terms(apps, None)
		self.assertEqual(self.terms(subject), ["multiple sclerosis"])

	def test_the_seed_leaves_no_history_row(self):
		subject = self.subject("MS", ["Multiple Sclerosis"])
		before = Subject.history.filter(id=subject.pk).count()
		seed_terms(apps, None)
		self.assertEqual(Subject.history.filter(id=subject.pk).count(), before)
