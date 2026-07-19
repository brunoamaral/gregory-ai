"""Tests for gregory.admin.SponsorMergeCandidateAdmin's merge/dismiss actions (PR D2).

Run:
	docker exec gregory python manage.py test gregory.tests.test_sponsor_merge_candidate_admin
"""

import re

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse

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

	def test_merge_snapshots_absorbed_name_and_nulls_its_own_fk(self):
		target = self._sponsor(
			"Target Corp 3", "target-corp-admin-3", sponsor_type="industry", sponsor_type_source="curated"
		)
		absorbed = self._sponsor("Absorbed Corp 3", "absorbed-corp-admin-3")
		candidate = self._pair(target, absorbed)

		self.admin.merge_action(
			self._request(), SponsorMergeCandidate.objects.filter(pk=candidate.pk)
		)

		candidate.refresh_from_db()
		self.assertEqual(candidate.status, "merged")
		self.assertEqual(candidate.absorbed_sponsor_name, "Absorbed Corp 3")
		# Never a (target, target) self-pair: exactly one side survives non-null, the
		# other was cleared by SET_NULL when the absorbed sponsor was deleted.
		remaining_ids = {candidate.sponsor_a_id, candidate.sponsor_b_id}
		self.assertIn(target.pk, remaining_ids)
		self.assertIn(None, remaining_ids)

	def test_second_merge_into_same_target_does_not_raise_integrity_error(self):
		# Regression test: merging two different candidates into the same survivor used
		# to repoint both merged rows' FKs onto the target, so the second merge's
		# unique_sponsor_candidate_pair check saw (target, target) already used by the
		# first row and raised IntegrityError.
		target = self._sponsor(
			"Target Corp 4", "target-corp-admin-4", sponsor_type="industry", sponsor_type_source="curated"
		)
		first_absorbed = self._sponsor("First Absorbed Corp 4", "first-absorbed-admin-4")
		second_absorbed = self._sponsor("Second Absorbed Corp 4", "second-absorbed-admin-4")
		first_candidate = self._pair(target, first_absorbed, shared_key="first")
		second_candidate = self._pair(target, second_absorbed, shared_key="second")

		self.admin.merge_action(
			self._request(), SponsorMergeCandidate.objects.filter(pk=first_candidate.pk)
		)
		self.admin.merge_action(
			self._request(), SponsorMergeCandidate.objects.filter(pk=second_candidate.pk)
		)

		first_candidate.refresh_from_db()
		second_candidate.refresh_from_db()
		self.assertEqual(first_candidate.status, "merged")
		self.assertEqual(second_candidate.status, "merged")
		self.assertFalse(Sponsor.objects.filter(pk__in=[first_absorbed.pk, second_absorbed.pk]).exists())
		self.assertTrue(Sponsor.objects.filter(pk=target.pk).exists())

	def test_changelist_display_does_not_crash_on_merged_row_with_null_side(self):
		target = self._sponsor(
			"Target Corp 5", "target-corp-admin-5", sponsor_type="industry", sponsor_type_source="curated"
		)
		absorbed = self._sponsor("Absorbed Corp 5", "absorbed-corp-admin-5")
		candidate = self._pair(target, absorbed)

		self.admin.merge_action(
			self._request(), SponsorMergeCandidate.objects.filter(pk=candidate.pk)
		)

		annotated = self.admin.get_queryset(self._request()).get(pk=candidate.pk)
		self.assertIn("merged away: Absorbed Corp 5", self.admin.sponsor_a_display(annotated) + self.admin.sponsor_b_display(annotated))
		self.assertIn("Target Corp 5", self.admin.sponsor_a_display(annotated) + self.admin.sponsor_b_display(annotated))

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


class SponsorMergeCandidateChangelistFilterTests(TestCase):
	"""Regression test: the changelist_view default-filter injection used to set the
	bare "status" query param, but the status field's choices= makes Django render its
	sidebar filter with ChoicesFieldListFilter, whose param is "status__exact" — so the
	stale "status=pending" survived into every filter link the sidebar generated
	(e.g. clicking "Merged" produced "?status=pending&status__exact=merged", a
	self-contradictory AND filter that always returned zero rows)."""

	def setUp(self):
		self.superuser = User.objects.create_superuser(
			username="filter-root", email="filter-root@example.com", password="pw"
		)
		self.client.force_login(self.superuser)
		self.url = reverse("admin:gregory_sponsormergecandidate_changelist")

	def _sponsor(self, name, slug, **extra):
		return Sponsor.objects.create(name=name, slug=slug, **extra)

	def test_default_view_shows_pending_only(self):
		a = self._sponsor("Filter Pending A", "filter-pending-a")
		b = self._sponsor("Filter Pending B", "filter-pending-b")
		c = self._sponsor("Filter Merged C", "filter-merged-c")
		SponsorMergeCandidate.objects.create(
			sponsor_a=a, sponsor_b=b, basis="suffix_variant", shared_key="pending-pair"
		)
		SponsorMergeCandidate.objects.create(
			sponsor_a=c, sponsor_b=None, basis="suffix_variant", shared_key="merged-pair",
			status="merged", absorbed_sponsor_name="Whatever Corp",
		)

		resp = self.client.get(self.url)

		self.assertContains(resp, "Filter Pending A")
		self.assertNotContains(resp, "Filter Merged C")

	def test_rendered_merged_filter_link_is_not_self_contradictory(self):
		a = self._sponsor("Filter Pending A2", "filter-pending-a2")
		b = self._sponsor("Filter Pending B2", "filter-pending-b2")
		c = self._sponsor("Filter Merged C2", "filter-merged-c2")
		SponsorMergeCandidate.objects.create(
			sponsor_a=a, sponsor_b=b, basis="suffix_variant", shared_key="pending-pair-2"
		)
		SponsorMergeCandidate.objects.create(
			sponsor_a=c, sponsor_b=None, basis="suffix_variant", shared_key="merged-pair-2",
			status="merged", absorbed_sponsor_name="Whatever Corp 2",
		)

		default_resp = self.client.get(self.url)
		body = default_resp.content.decode()
		match = re.search(r'href="(\?[^"]*status__exact=merged[^"]*)"', body)
		self.assertIsNotNone(match, "changelist did not render a 'Merged' filter link")
		merged_link = match.group(1).replace("&amp;", "&")

		# The bug: the rendered link itself still carried the stale bare "status=pending"
		# alongside "status__exact=merged".
		self.assertNotIn("status=pending", merged_link)

		merged_resp = self.client.get(self.url + merged_link)
		self.assertContains(merged_resp, "Filter Merged C2")
		self.assertNotContains(merged_resp, "Filter Pending A2")


class SponsorMergeCandidateChoicesTests(TestCase):
	def test_basis_and_status_reject_invalid_values_on_full_clean(self):
		a = Sponsor.objects.create(name="Choices Corp A", slug="choices-corp-a")
		b = Sponsor.objects.create(name="Choices Corp B", slug="choices-corp-b")
		candidate = SponsorMergeCandidate(
			sponsor_a=a, sponsor_b=b, basis="not_a_real_basis", shared_key="k", status="not_a_real_status"
		)

		with self.assertRaises(ValidationError) as ctx:
			candidate.full_clean()

		self.assertIn("basis", ctx.exception.message_dict)
		self.assertIn("status", ctx.exception.message_dict)
