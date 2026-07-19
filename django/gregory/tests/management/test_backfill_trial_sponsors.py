"""Tests for the backfill_trial_sponsors management command.

Run:
	docker exec gregory python manage.py test gregory.tests.management.test_backfill_trial_sponsors
"""

import os
from io import StringIO

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from gregory.models import Sponsor, SponsorAlias, Trials


class BackfillTrialSponsorsTests(TestCase):
	def run_command(self, **kwargs):
		out, err = StringIO(), StringIO()
		call_command("backfill_trial_sponsors", stdout=out, stderr=err, **kwargs)
		return out.getvalue(), err.getvalue()

	def _make_stale(self, n, primary_sponsor, **extra):
		"""Create a trial via bulk-style update() so it bypasses Trials.save()'s sponsor
		resolution entirely — simulating a row written before that hook existed."""
		trial = Trials.objects.create(
			title=f"Trial {n}", link=f"https://example.com/backfill-sponsor-{n}"
		)
		Trials.objects.filter(pk=trial.pk).update(primary_sponsor=primary_sponsor, **extra)
		trial.refresh_from_db()
		return trial

	def test_resolves_stale_trials(self):
		t1 = self._make_stale(1, "Acme Research Corp")
		t2 = self._make_stale(2, "Acme Research Corp")
		t3 = self._make_stale(3, None)

		out, _ = self.run_command()

		t1.refresh_from_db()
		t2.refresh_from_db()
		t3.refresh_from_db()
		self.assertIsNotNone(t1.primary_sponsor_normalized)
		self.assertEqual(t1.primary_sponsor_normalized_id, t2.primary_sponsor_normalized_id)
		self.assertIsNone(t3.primary_sponsor_normalized)
		self.assertIn("resolve 2 to a sponsor", out)
		self.assertIn("1 left with no sponsor", out)
		self.assertIn("Created 1 new sponsor(s)", out)

	def test_idempotent_rerun_writes_nothing(self):
		self._make_stale(1, "Acme Research Corp")
		self.run_command()

		out, _ = self.run_command()

		self.assertIn("resolve 1 to a sponsor (0 FK write(s))", out)
		self.assertIn("Created 0 new sponsor(s)", out)

	def test_dry_run_persists_nothing(self):
		self._make_stale(1, "Acme Research Corp")

		out, _ = self.run_command(dry_run=True)

		self.assertFalse(Sponsor.objects.exists())
		trial = Trials.objects.get(title="Trial 1")
		self.assertIsNone(trial.primary_sponsor_normalized)
		self.assertIn("Would create 1 new sponsor(s)", out)
		self.assertIn("Would resolve 1 to a sponsor", out)

	def test_derives_sponsor_type_for_non_curated_sponsor(self):
		self._make_stale(1, "Acme Foundation")

		self.run_command()

		sponsor = Sponsor.objects.get(name="Acme Foundation")
		self.assertEqual(sponsor.sponsor_type, "nonprofit")
		self.assertEqual(sponsor.sponsor_type_source, "rules")

	def test_never_overwrites_curated_sponsor_type(self):
		sponsor = Sponsor.objects.create(
			name="Curated Corp",
			slug="curated-corp",
			sponsor_type="government",
			sponsor_type_source="curated",
		)
		SponsorAlias.objects.create(
			sponsor=sponsor, key="curated corp", raw_sample="Curated Corp"
		)
		self._make_stale(1, "Curated Corp", lead_sponsor_class="INDUSTRY")

		self.run_command()

		sponsor.refresh_from_db()
		self.assertEqual(sponsor.sponsor_type, "government")
		self.assertEqual(sponsor.sponsor_type_source, "curated")

	def test_bounded_query_count_not_one_per_trial(self):
		for i in range(30):
			self._make_stale(i, f"Bounded Sponsor {i % 5}")

		with CaptureQueriesContext(connection) as ctx:
			self.run_command()

		# A handful of fixed-cost queries (alias map load, sponsor load, the streamed
		# scan, a few batched bulk_create/bulk_update calls) — not ~30 (one per trial).
		self.assertLess(len(ctx.captured_queries), 20)
