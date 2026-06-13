"""
Tests for the merge_trials management command (Phase-0 duplicate resolution).

Verifies that merging a duplicate trial into a kept trial:
  - unions identifiers (kept trial wins on shared keys, gains missing keys, skips nulls),
  - moves M2M links (teams, subjects, …) to the kept trial,
  - repoints reverse-FK children (e.g. TrialOrgContent) instead of CASCADE-deleting them,
  - drops a child that would collide with an existing row on the kept trial,
  - leaves everything untouched under --dry-run.

Run:
  docker exec gregory python manage.py test gregory.tests.management.test_merge_trials
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.core.management import call_command
from django.test import TestCase
from organizations.models import Organization

from gregory.models import Trials, Team, Subject, TrialOrgContent


class MergeTrialsTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Merge Org", slug="merge-org")
		self.team = Team.objects.create(
			organization=self.org, name="Merge Team", slug="merge-team"
		)
		self.subject = Subject.objects.create(
			subject_name="MS", subject_slug="ms", team=self.team
		)

	def _trial(self, title, identifiers, link="https://example.com/x"):
		return Trials.objects.create(title=title, link=link, identifiers=identifiers)

	def test_merge_unions_identifiers_moves_m2m_and_reverse_fk(self):
		keep = self._trial("Keep One", {"nct": "NCT00000001", "ctis": "CTIS-1"})
		rem = self._trial(
			"Rem One", {"nct": "NCT00000001b", "euctr": "EUCTR-1", "euct": None}
		)
		rem.teams.add(self.team)
		rem.subjects.add(self.subject)
		# reverse-FK child only on rem → must be repointed, not lost
		TrialOrgContent.objects.create(
			trial=rem, organization=self.org, takeaways="keepme"
		)

		call_command("merge_trials", keep=keep.pk, remove=[rem.pk])

		self.assertFalse(Trials.objects.filter(pk=rem.pk).exists())
		keep.refresh_from_db()
		# kept trial wins on 'nct', gains 'euctr', skips the null 'euct'
		self.assertEqual(
			keep.identifiers,
			{"nct": "NCT00000001", "ctis": "CTIS-1", "euctr": "EUCTR-1"},
		)
		self.assertTrue(keep.teams.filter(pk=self.team.pk).exists())
		self.assertTrue(keep.subjects.filter(pk=self.subject.pk).exists())
		self.assertEqual(keep.org_contents.count(), 1)
		self.assertEqual(keep.org_contents.first().takeaways, "keepme")

	def test_merge_drops_colliding_reverse_fk_child(self):
		keep = self._trial("Keep Two", {"nct": "NCT00000002"})
		rem = self._trial("Rem Two", {"nct": "NCT00000002b"})
		# both have org content for the SAME org → unique (trial, org) collision on repoint
		TrialOrgContent.objects.create(
			trial=keep, organization=self.org, takeaways="keep-content"
		)
		TrialOrgContent.objects.create(
			trial=rem, organization=self.org, takeaways="rem-content"
		)

		call_command("merge_trials", keep=keep.pk, remove=[rem.pk])

		self.assertFalse(Trials.objects.filter(pk=rem.pk).exists())
		# kept trial retains exactly its own org content; rem's was dropped, not a crash
		self.assertEqual(keep.org_contents.count(), 1)
		self.assertEqual(keep.org_contents.first().takeaways, "keep-content")

	def test_dry_run_changes_nothing(self):
		keep = self._trial("Keep Three", {"nct": "NCT00000003"})
		rem = self._trial("Rem Three", {"nct": "NCT00000003b"})
		call_command("merge_trials", keep=keep.pk, remove=[rem.pk], dry_run=True)
		self.assertTrue(Trials.objects.filter(pk=rem.pk).exists())
		self.assertTrue(Trials.objects.filter(pk=keep.pk).exists())
