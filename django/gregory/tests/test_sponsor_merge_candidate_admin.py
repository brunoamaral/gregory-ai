"""Tests for gregory.admin.SponsorMergeCandidateAdmin's merge/dismiss actions (PR D2).

Run:
	docker exec gregory python manage.py test gregory.tests.test_sponsor_merge_candidate_admin
"""

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase

from gregory.admin import SponsorMergeCandidateAdmin
from gregory.models import Sponsor, SponsorMergeCandidate, Trials

User = get_user_model()


class SponsorMergeCandidateAdminTests(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.site = AdminSite()
		self.admin = SponsorMergeCandidateAdmin(SponsorMergeCandidate, self.site)
		self.superuser = User.objects.create_superuser(
			username="root", email="r@e.com", password="pw"
		)

	def _request(self):
		request = self.factory.post("/admin/gregory/sponsormergecandidate/")
		request.user = self.superuser
		request.session = {}
		request._messages = FallbackStorage(request)
		return request

	def _sponsor(self, name, slug, **extra):
		return Sponsor.objects.create(name=name, slug=slug, **extra)

	def _trial(self, sponsor, link):
		trial = Trials.objects.create(title=f"T-{link}", link=link, primary_sponsor=None)
		Trials.objects.filter(pk=trial.pk).update(primary_sponsor_normalized=sponsor)
		return trial

	def _pair(self, a, b, basis="suffix_variant", shared_key="k"):
		a_id, b_id = sorted([a.pk, b.pk])
		return SponsorMergeCandidate.objects.create(
			sponsor_a_id=a_id, sponsor_b_id=b_id, basis=basis, shared_key=shared_key
		)

	def test_merge_picks_curated_target_over_larger_uncurated(self):
		curated = self._sponsor(
			"Bayer AG", "bayer-ag-admin", sponsor_type="industry", sponsor_type_source="curated"
		)
		bigger_uncurated = self._sponsor("Bayer", "bayer-admin")
		self._trial(bigger_uncurated, "https://example.com/admin-1")
		self._trial(bigger_uncurated, "https://example.com/admin-2")
		candidate = self._pair(curated, bigger_uncurated)

		self.admin.merge_action(
			self._request(), SponsorMergeCandidate.objects.filter(pk=candidate.pk)
		)

		self.assertFalse(Sponsor.objects.filter(pk=bigger_uncurated.pk).exists())
		self.assertTrue(Sponsor.objects.filter(pk=curated.pk).exists())
		for trial in Trials.objects.filter(link__startswith="https://example.com/admin-"):
			self.assertEqual(trial.primary_sponsor_normalized_id, curated.pk)
		candidate.refresh_from_db()
		self.assertEqual(candidate.status, "merged")
		self.assertIsNotNone(candidate.decided_at)

	def test_merge_picks_larger_trial_count_when_neither_curated(self):
		small = self._sponsor("Small Corp", "small-corp-admin")
		large = self._sponsor("Large Corp", "large-corp-admin")
		self._trial(large, "https://example.com/admin-3")
		self._trial(large, "https://example.com/admin-4")
		candidate = self._pair(small, large)

		self.admin.merge_action(
			self._request(), SponsorMergeCandidate.objects.filter(pk=candidate.pk)
		)

		self.assertFalse(Sponsor.objects.filter(pk=small.pk).exists())
		self.assertTrue(Sponsor.objects.filter(pk=large.pk).exists())

	def test_merge_sweeps_other_pending_candidate_referencing_absorbed_sponsor(self):
		target = self._sponsor(
			"Target Corp", "target-corp-admin", sponsor_type="industry", sponsor_type_source="curated"
		)
		absorbed = self._sponsor("Absorbed Corp", "absorbed-corp-admin")
		third_party = self._sponsor("Third Party Corp", "third-party-admin")

		primary = self._pair(target, absorbed)
		other_pending = self._pair(absorbed, third_party, basis="containment", shared_key="absorbed")

		self.admin.merge_action(
			self._request(), SponsorMergeCandidate.objects.filter(pk=primary.pk)
		)

		other_pending.refresh_from_db()
		self.assertEqual(other_pending.status, "pending")
		self.assertEqual(
			{other_pending.sponsor_a_id, other_pending.sponsor_b_id},
			{target.pk, third_party.pk},
		)

	def test_merge_sweep_deletes_stale_duplicate_instead_of_repointing(self):
		target = self._sponsor(
			"Target Corp 2", "target-corp-admin-2", sponsor_type="industry", sponsor_type_source="curated"
		)
		absorbed = self._sponsor("Absorbed Corp 2", "absorbed-corp-admin-2")
		third_party = self._sponsor("Third Party Corp 2", "third-party-admin-2")

		primary = self._pair(target, absorbed)
		# Already an existing (target, third_party) pair — the swept row would duplicate it.
		self._pair(target, third_party, basis="suffix_variant", shared_key="dup")
		stale = self._pair(absorbed, third_party, basis="containment", shared_key="absorbed2")

		self.admin.merge_action(
			self._request(), SponsorMergeCandidate.objects.filter(pk=primary.pk)
		)

		self.assertFalse(SponsorMergeCandidate.objects.filter(pk=stale.pk).exists())

	def test_dismiss_flips_status_and_is_not_regenerated_on_rerun(self):
		a = self._sponsor("Aalborg University Admin", "aalborg-uni-admin")
		b = self._sponsor("Aalborg University Hospital Admin", "aalborg-hosp-admin")
		candidate = self._pair(a, b, basis="containment", shared_key="aalborg university admin")

		self.admin.dismiss_action(
			self._request(), SponsorMergeCandidate.objects.filter(pk=candidate.pk)
		)

		candidate.refresh_from_db()
		self.assertEqual(candidate.status, "dismissed")
		self.assertIsNotNone(candidate.decided_at)
		# Both sponsors remain untouched — dismiss never merges.
		self.assertTrue(Sponsor.objects.filter(pk__in=[a.pk, b.pk]).count() == 2)

		from django.core.management import call_command

		call_command("find_sponsor_merge_candidates")
		self.assertEqual(SponsorMergeCandidate.objects.filter(pk=candidate.pk).count(), 1)
		candidate.refresh_from_db()
		self.assertEqual(candidate.status, "dismissed")
