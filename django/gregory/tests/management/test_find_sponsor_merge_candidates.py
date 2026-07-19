"""Tests for the find_sponsor_merge_candidates management command (PR D2).

Run:
	docker exec gregory python manage.py test gregory.tests.management.test_find_sponsor_merge_candidates
"""

import os
from io import StringIO

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.core.management import call_command
from django.test import TestCase

from gregory.models import Sponsor, SponsorMergeCandidate, Trials


class FindSponsorMergeCandidatesTests(TestCase):
	def run_command(self):
		out, err = StringIO(), StringIO()
		call_command("find_sponsor_merge_candidates", stdout=out, stderr=err)
		return out.getvalue(), err.getvalue()

	def _sponsor(self, name, slug, **extra):
		return Sponsor.objects.create(name=name, slug=slug, **extra)

	def _trial(self, sponsor, link):
		trial = Trials.objects.create(title=f"T-{link}", link=link, primary_sponsor=None)
		Trials.objects.filter(pk=trial.pk).update(primary_sponsor_normalized=sponsor)
		return trial

	def test_suffix_variant_group_found(self):
		bayer = self._sponsor("Bayer", "bayer")
		bayer_ag = self._sponsor("Bayer AG", "bayer-ag")
		self._trial(bayer, "https://example.com/d2-1")
		self._trial(bayer, "https://example.com/d2-2")

		out, _ = self.run_command()

		self.assertIn("suffix_variant candidate", out)
		candidate = SponsorMergeCandidate.objects.get(basis="suffix_variant")
		self.assertEqual(candidate.status, "pending")
		self.assertEqual({candidate.sponsor_a_id, candidate.sponsor_b_id}, {bayer.pk, bayer_ag.pk})
		self.assertEqual(candidate.sponsor_a_id, min(bayer.pk, bayer_ag.pk))

	def test_merck_family_generates_candidates_without_auto_merging(self):
		merck = self._sponsor("Merck", "merck")
		merck_ab = self._sponsor("Merck AB", "merck-ab")
		merck_kgaa = self._sponsor("Merck KGaA", "merck-kgaa")
		self._trial(merck_kgaa, "https://example.com/d2-merck-1")

		self.run_command()

		self.assertTrue(SponsorMergeCandidate.objects.filter(basis="suffix_variant").exists())
		# Never auto-merges: all three sponsors must still exist untouched.
		self.assertEqual(
			Sponsor.objects.filter(pk__in=[merck.pk, merck_ab.pk, merck_kgaa.pk]).count(), 3
		)
		self.assertTrue(
			SponsorMergeCandidate.objects.filter(status="pending").exists()
		)
		self.assertFalse(SponsorMergeCandidate.objects.filter(status="merged").exists())

	def test_containment_noise_filter_excludes_of_continuation(self):
		self._sponsor("University", "university")
		self._sponsor("University of California, Los Angeles", "ucla")

		self.run_command()

		self.assertFalse(SponsorMergeCandidate.objects.filter(basis="containment").exists())

	def test_containment_pair_generated_for_genuine_subset(self):
		aalborg_uni = self._sponsor("Aalborg University", "aalborg-university")
		aalborg_hospital = self._sponsor("Aalborg University Hospital", "aalborg-hospital")

		self.run_command()

		candidate = SponsorMergeCandidate.objects.get(basis="containment")
		self.assertEqual(
			{candidate.sponsor_a_id, candidate.sponsor_b_id},
			{aalborg_uni.pk, aalborg_hospital.pk},
		)

	def test_dismissed_pair_is_not_regenerated(self):
		bayer = self._sponsor("Bayer", "bayer-2")
		bayer_ag = self._sponsor("Bayer AG", "bayer-ag-2")

		self.run_command()
		candidate = SponsorMergeCandidate.objects.get(basis="suffix_variant")
		candidate.status = "dismissed"
		candidate.save(update_fields=["status"])

		self.run_command()

		self.assertEqual(SponsorMergeCandidate.objects.filter(basis="suffix_variant").count(), 1)
		candidate.refresh_from_db()
		self.assertEqual(candidate.status, "dismissed")

	def test_pair_order_normalized_and_unique_constraint_enforced(self):
		bayer = self._sponsor("Bayer", "bayer-3")
		bayer_ag = self._sponsor("Bayer AG", "bayer-ag-3")
		lower, higher = sorted([bayer, bayer_ag], key=lambda s: s.pk)

		self.run_command()

		candidate = SponsorMergeCandidate.objects.get(basis="suffix_variant")
		self.assertEqual(candidate.sponsor_a_id, lower.pk)
		self.assertEqual(candidate.sponsor_b_id, higher.pk)

		from django.db import IntegrityError, transaction

		with self.assertRaises(IntegrityError):
			with transaction.atomic():
				SponsorMergeCandidate.objects.create(
					sponsor_a_id=lower.pk,
					sponsor_b_id=higher.pk,
					basis="suffix_variant",
					shared_key="bayer",
				)

	def test_second_run_creates_no_duplicate_pending_rows(self):
		self._sponsor("Bayer", "bayer-4")
		self._sponsor("Bayer AG", "bayer-ag-4")

		self.run_command()
		first_count = SponsorMergeCandidate.objects.count()
		self.run_command()
		second_count = SponsorMergeCandidate.objects.count()

		self.assertEqual(first_count, second_count)
