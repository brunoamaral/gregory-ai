"""Tests for the recompute_sponsor_alias_keys management command (PR D1).

Run:
	docker exec gregory python manage.py test gregory.tests.management.test_recompute_sponsor_alias_keys
"""

import os
from io import StringIO

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.core.management import call_command
from django.test import TestCase

from gregory.models import Sponsor, SponsorAlias, Trials


class RecomputeSponsorAliasKeysTests(TestCase):
	def run_command(self, **kwargs):
		out, err = StringIO(), StringIO()
		call_command("recompute_sponsor_alias_keys", stdout=out, stderr=err, **kwargs)
		return out.getvalue(), err.getvalue()

	def _sponsor(self, name, slug, **extra):
		return Sponsor.objects.create(name=name, slug=slug, **extra)

	def _alias(self, sponsor, key, raw_sample):
		return SponsorAlias.objects.create(sponsor=sponsor, key=key, raw_sample=raw_sample)

	def test_folds_punctuation_only_duplicate_group(self):
		a = self._sponsor("1st Biotherapeutics Inc.", "1st-bio-a")
		b = self._sponsor("1ST Biotherapeutics, Inc.", "1st-bio-b")
		# Stale (pre-hardening) keys: distinct because the old function only stripped
		# trailing punctuation.
		self._alias(a, "1st biotherapeutics inc", "1st Biotherapeutics Inc.")
		self._alias(b, "1st biotherapeutics, inc", "1ST Biotherapeutics, Inc.")
		trial = Trials.objects.create(
			title="Trial under b", link="https://example.com/fold-1", primary_sponsor=None
		)
		Trials.objects.filter(pk=trial.pk).update(primary_sponsor_normalized=b)

		out, _ = self.run_command()

		self.assertIn("FOLD:", out)
		remaining = Sponsor.objects.filter(pk__in=[a.pk, b.pk])
		self.assertEqual(remaining.count(), 1)
		trial.refresh_from_db()
		self.assertEqual(trial.primary_sponsor_normalized_id, remaining.get().pk)

	def test_curated_sponsor_wins_over_larger_uncurated_duplicate(self):
		curated = self._sponsor(
			"Bristol-Myers Squibb",
			"bms-curated",
			sponsor_type="industry",
			sponsor_type_source="curated",
		)
		bigger_uncurated = self._sponsor("Bristol Myers Squibb", "bms-bigger")
		self._alias(curated, "bristol-myers squibb", "Bristol-Myers Squibb")
		self._alias(bigger_uncurated, "bristol myers squibb", "Bristol Myers Squibb")
		for i in range(5):
			t = Trials.objects.create(
				title=f"BMS trial {i}",
				link=f"https://example.com/bms-{i}",
				primary_sponsor=None,
			)
			Trials.objects.filter(pk=t.pk).update(primary_sponsor_normalized=bigger_uncurated)

		self.run_command()

		self.assertTrue(Sponsor.objects.filter(pk=curated.pk).exists())
		self.assertFalse(Sponsor.objects.filter(pk=bigger_uncurated.pk).exists())
		curated.refresh_from_db()
		self.assertEqual(curated.trials.count(), 5)

	def test_non_colliding_sponsors_are_left_alone(self):
		a = self._sponsor("Aalborg University", "aalborg-uni")
		b = self._sponsor("Aalborg University Hospital", "aalborg-hosp")
		self._alias(a, "aalborg university", "Aalborg University")
		self._alias(b, "aalborg university hospital", "Aalborg University Hospital")

		out, _ = self.run_command()

		self.assertNotIn("FOLD:", out)
		self.assertTrue(Sponsor.objects.filter(pk=a.pk).exists())
		self.assertTrue(Sponsor.objects.filter(pk=b.pk).exists())

	def test_alias_keys_are_rewritten_to_hardened_value(self):
		sponsor = self._sponsor("Genentech, Inc", "genentech")
		alias = self._alias(sponsor, "genentech, inc", "Genentech, Inc")

		self.run_command()

		alias.refresh_from_db()
		self.assertEqual(alias.key, "genentech inc")

	def test_intra_sponsor_duplicate_aliases_collapse_after_hardening(self):
		sponsor = self._sponsor("Huashan Hospital, Fudan University", "huashan")
		self._alias(sponsor, "huashan hospital, fudan university", "Huashan Hospital, Fudan University")
		self._alias(sponsor, "huashan hospital,  fudan university", "Huashan Hospital,  Fudan University")

		self.run_command()

		self.assertEqual(SponsorAlias.objects.filter(sponsor=sponsor).count(), 1)

	def test_second_run_is_a_no_op(self):
		a = self._sponsor("1st Biotherapeutics Inc.", "1st-bio-a2")
		b = self._sponsor("1ST Biotherapeutics, Inc.", "1st-bio-b2")
		self._alias(a, "1st biotherapeutics inc", "1st Biotherapeutics Inc.")
		self._alias(b, "1st biotherapeutics, inc", "1ST Biotherapeutics, Inc.")

		self.run_command()
		out, _ = self.run_command()

		self.assertIn("Folded 0 group(s)", out)
		self.assertIn("Re-keyed 0 alias(es)", out)
		self.assertIn("removed 0 now-duplicate alias(es)", out)

	def test_dry_run_persists_nothing(self):
		a = self._sponsor("1st Biotherapeutics Inc.", "1st-bio-a3")
		b = self._sponsor("1ST Biotherapeutics, Inc.", "1st-bio-b3")
		self._alias(a, "1st biotherapeutics inc", "1st Biotherapeutics Inc.")
		self._alias(b, "1st biotherapeutics, inc", "1ST Biotherapeutics, Inc.")

		out, _ = self.run_command(dry_run=True)

		self.assertIn("DRY RUN", out)
		self.assertTrue(Sponsor.objects.filter(pk=a.pk).exists())
		self.assertTrue(Sponsor.objects.filter(pk=b.pk).exists())
