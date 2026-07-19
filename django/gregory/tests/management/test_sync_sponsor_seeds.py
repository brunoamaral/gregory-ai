"""Tests for the sync_sponsor_seeds management command.

Uses a small patched SPONSOR_SEEDS fixture (rather than the real, large one) so these
tests stay fast and are not coupled to future edits of the curated family list. The real
SPONSOR_SEEDS' own collision/merge-trap guards are covered separately in
gregory/tests/test_sponsor_canonicalization.py::SponsorSeedGuardTests.

Run:
	docker exec gregory python manage.py test gregory.tests.management.test_sync_sponsor_seeds
"""

import os
from io import StringIO
from unittest.mock import patch

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.core.management import call_command
from django.test import TestCase

from gregory.models import Sponsor, SponsorAlias, Trials

FIXTURE_SEEDS = {
	"Acme Corp": (
		"industry",
		["Acme Corp", "Acme Corporation", "Acme Corp Ltd"],
	),
}

SEEDS_TARGET = "gregory.management.commands.sync_sponsor_seeds.SPONSOR_SEEDS"


@patch(SEEDS_TARGET, FIXTURE_SEEDS)
class SyncSponsorSeedsTests(TestCase):
	def run_command(self, **kwargs):
		out, err = StringIO(), StringIO()
		call_command("sync_sponsor_seeds", stdout=out, stderr=err, **kwargs)
		return out.getvalue(), err.getvalue()

	def test_creates_canonical_sponsor_and_aliases(self):
		out, _ = self.run_command()

		sponsor = Sponsor.objects.get(name="Acme Corp")
		self.assertEqual(sponsor.sponsor_type, "industry")
		self.assertEqual(sponsor.sponsor_type_source, "curated")
		self.assertEqual(sponsor.aliases.count(), 3)
		self.assertIn("Created 1 new canonical sponsor(s)", out)

	def test_idempotent_rerun_creates_nothing(self):
		self.run_command()
		out, _ = self.run_command()

		self.assertIn("Created 0 new canonical sponsor(s), 0 new alias(es)", out)
		self.assertEqual(Sponsor.objects.filter(name="Acme Corp").count(), 1)

	def test_dry_run_persists_nothing(self):
		out, _ = self.run_command(dry_run=True)

		self.assertFalse(Sponsor.objects.filter(name="Acme Corp").exists())
		self.assertIn("Would create 1 new canonical sponsor(s), 3 new alias(es)", out)

	def test_repoints_and_folds_a_previously_auto_created_sponsor(self):
		# Simulate a trial having already auto-created its own singleton sponsor for one
		# of the family's variant spellings, before the seed family existed.
		trial = Trials.objects.create(
			title="Pre-seed trial",
			link="https://example.com/sync-seeds-1",
			primary_sponsor="Acme Corporation",
		)
		stray_sponsor = trial.primary_sponsor_normalized
		self.assertEqual(stray_sponsor.name, "Acme Corporation")

		out, _ = self.run_command()

		trial.refresh_from_db()
		canonical = Sponsor.objects.get(name="Acme Corp")
		self.assertEqual(trial.primary_sponsor_normalized_id, canonical.pk)
		self.assertFalse(Sponsor.objects.filter(pk=stray_sponsor.pk).exists())
		self.assertIn("Folding stray sponsor", out)
		self.assertIn("repointing 1 trial(s)", out)

	def test_moves_aliases_of_folded_sponsor(self):
		stray = Sponsor.objects.create(name="Acme Corporation", slug="acme-corporation")
		stray_alias = SponsorAlias.objects.create(
			sponsor=stray, key="acme corporation", raw_sample="Acme Corporation"
		)

		self.run_command()

		stray_alias.refresh_from_db()
		canonical = Sponsor.objects.get(name="Acme Corp")
		self.assertEqual(stray_alias.sponsor_id, canonical.pk)
