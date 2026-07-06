"""
Tests for corroborated org_study_id trial matching (pipeline audit, item 1.3).

Org study IDs are sponsor protocol codes and not globally unique, so an exact
match alone must not merge. Merging requires the identifiers_conflict guard
plus a corroborating signal: a shared registry-key value (nct/euct/eudract/
ctis) or an exact title match. The old secondary_id__icontains substring match
is gone. Cross-registry identifier coexistence (disjoint keys) is never a
conflict.

Run:
  docker exec gregory python manage.py test gregory.tests.test_ctgov_trial_matching
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import TestCase

from gregory.classes import ClinicalTrial
from gregory.management.commands.feedreader_trials_ctgov import Command
from gregory.models import Trials


def incoming(title="Incoming trial", **identifiers):
	identifiers.setdefault("nct", "NCT11111111")
	return ClinicalTrial(
		title=title,
		summary="s",
		link=f"https://clinicaltrials.gov/study/{identifiers['nct']}",
		published_date=None,
		identifiers=identifiers,
		extra_fields={},
	)


class OrgStudyIdMatchingTests(TestCase):
	def setUp(self):
		self.cmd = Command()
		self.cmd.debug = False

	def test_org_study_id_alone_does_not_merge(self):
		Trials.objects.create(
			title="A completely different trial",
			link="https://euclinicaltrials.eu/app/trial/1",
			identifiers={"org_study_id": "MS-001"},
		)
		trial = self.cmd.find_existing_trial(
			incoming(org_study_id="MS-001")
		)
		self.assertIsNone(trial)

	def test_org_study_id_with_shared_registry_key_merges(self):
		existing = Trials.objects.create(
			title="A completely different trial",
			link="https://euclinicaltrials.eu/app/trial/1",
			identifiers={"org_study_id": "MS-001", "eudract": "2020-000001-01"},
		)
		trial = self.cmd.find_existing_trial(
			incoming(org_study_id="MS-001", eudract="2020-000001-01")
		)
		self.assertEqual(trial, existing)

	def test_org_study_id_with_exact_title_match_merges(self):
		existing = Trials.objects.create(
			title="Shared protocol trial",
			link="https://euclinicaltrials.eu/app/trial/1",
			identifiers={"org_study_id": "MS-001"},
		)
		trial = self.cmd.find_existing_trial(
			incoming(title="Shared Protocol Trial", org_study_id="MS-001")
		)
		self.assertEqual(trial, existing)

	def test_conflicting_registry_key_never_merges(self):
		# Same org_study_id and even the same title, but the SAME registry key
		# holds a DIFFERENT value → different trials.
		Trials.objects.create(
			title="Shared protocol trial",
			link="https://euclinicaltrials.eu/app/trial/1",
			identifiers={"org_study_id": "MS-001", "eudract": "2020-000001-01"},
		)
		trial = self.cmd.find_existing_trial(
			incoming(
				title="Shared protocol trial",
				org_study_id="MS-001",
				eudract="2021-999999-99",
			)
		)
		self.assertIsNone(trial)

	def test_disjoint_registry_keys_are_not_a_conflict(self):
		# Cross-registered study: candidate has euct, incoming has eudract —
		# disjoint keys plus a title corroboration must merge.
		existing = Trials.objects.create(
			title="Cross registered trial",
			link="https://euclinicaltrials.eu/app/trial/1",
			identifiers={"org_study_id": "MS-001", "euct": "2022-500014-26-00"},
		)
		trial = self.cmd.find_existing_trial(
			incoming(
				title="Cross registered trial",
				org_study_id="MS-001",
				eudract="2020-000001-01",
			)
		)
		self.assertEqual(trial, existing)

	def test_secondary_id_substring_no_longer_matches(self):
		# The old Q(secondary_id__icontains=...) would have absorbed this.
		Trials.objects.create(
			title="Unrelated trial",
			link="https://euclinicaltrials.eu/app/trial/1",
			identifiers={},
			secondary_id="Protocol MS-001-EXT and others",
		)
		trial = self.cmd.find_existing_trial(
			incoming(org_study_id="MS-001")
		)
		self.assertIsNone(trial)

	def test_nct_match_still_wins_first(self):
		existing = Trials.objects.create(
			title="Whatever title",
			link="https://clinicaltrials.gov/study/NCT11111111",
			identifiers={"nct": "NCT11111111"},
		)
		trial = self.cmd.find_existing_trial(incoming(org_study_id="MS-001"))
		self.assertEqual(trial, existing)
