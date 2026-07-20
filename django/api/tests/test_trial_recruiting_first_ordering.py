"""Tests for ?ordering=recruiting_first on GET /trials/.

Fixture/org-visibility setup mirrors api/tests/test_trial_recruitment_status_normalization.py
— a public organization is required for the default (anonymous) APIClient to see any
trials at all.

recruiting_first sorts by recruitment *availability* (see api.views._RECRUITING_RANK),
not alphabetically on recruitment_status_normalized. See STATUS-ORDERING-PLAN.md.
"""

from datetime import datetime, timezone as dt_timezone

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APIClient

from api.views import _RECRUITING_RANK
from gregory.models import Organization, OrganizationApiSettings, Subject, Team, Trials
from gregory.utils.trial_field_normalizers import TrialRecruitmentStatus


def _make_org_team(name, slug):
	organization = Organization.objects.create(name=name, slug=slug)
	OrganizationApiSettings.objects.filter(organization=organization).update(
		make_api_public=True
	)
	team = Team.objects.create(name=name, slug=slug, organization=organization)
	return organization, team


def _make_trial(title, link, team, subject=None, status=None, discovery_date=None):
	trial = Trials.objects.create(
		title=title,
		link=link,
		recruitment_status=status,
		discovery_date=discovery_date,
	)
	trial.teams.add(team)
	if subject is not None:
		trial.subjects.add(subject)
	return trial


class RecruitingFirstRankCoverageTest(TestCase):
	"""Every TrialRecruitmentStatus member must have a rank, or a newly added
	status silently sorts as null (last) instead of failing loudly."""

	def test_every_recruitment_status_has_a_rank(self):
		for member in TrialRecruitmentStatus:
			with self.subTest(status=member.value):
				self.assertIn(
					member,
					_RECRUITING_RANK,
					msg=f"{member.value!r} has no entry in _RECRUITING_RANK — add one "
					"in api/views.py or it will silently sort last.",
				)

	def test_rank_has_no_stale_entries(self):
		valid = {member.value for member in TrialRecruitmentStatus}
		for key in _RECRUITING_RANK:
			self.assertIn(key.value, valid)


class RecruitingFirstOrderingTest(TestCase):
	def setUp(self):
		self.organization, self.team = _make_org_team(
			"Recruiting First Org", "recruiting-first-org"
		)
		self.subject = Subject.objects.create(
			subject_name="Recruiting First Subject",
			subject_slug="recruiting-first-subject",
			team=self.team,
		)

		base = datetime(2026, 1, 1, tzinfo=dt_timezone.utc)
		self.recruiting = _make_trial(
			"Recruiting",
			"https://example.com/recruiting-first-recruiting",
			self.team,
			self.subject,
			status="Recruiting",
			discovery_date=base,
		)
		self.completed = _make_trial(
			"Completed",
			"https://example.com/recruiting-first-completed",
			self.team,
			self.subject,
			status="Completed",
			discovery_date=base,
		)
		self.no_status = _make_trial(
			"No status",
			"https://example.com/recruiting-first-no-status",
			self.team,
			self.subject,
			status=None,
			discovery_date=base,
		)

		self.client = APIClient()

	def _trial_ids(self, response):
		return [row["trial_id"] for row in response.data["results"]]

	def test_recruiting_first_puts_recruiting_ahead_of_completed_and_null_last(self):
		response = self.client.get("/trials/?ordering=recruiting_first")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = self._trial_ids(response)
		self.assertEqual(
			ids,
			[self.recruiting.trial_id, self.completed.trial_id, self.no_status.trial_id],
		)

	def test_reverse_recruiting_first_reverses_order(self):
		response = self.client.get("/trials/?ordering=-recruiting_first")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = self._trial_ids(response)
		self.assertEqual(
			ids,
			[self.no_status.trial_id, self.completed.trial_id, self.recruiting.trial_id],
		)

	def test_full_rank_order_end_to_end(self):
		"""Create one trial per status (skipping the three already in setUp) and
		confirm the full accepted priority order from the plan."""
		base = datetime(2026, 1, 2, tzinfo=dt_timezone.utc)
		# Raw values that normalize_recruitment_status maps deterministically (see
		# gregory/utils/trial_field_normalizers.py's exact-match table) — plain
		# human-readable labels like "Not yet recruiting" don't all match a key
		# there and would fall back to OTHER, defeating the point of this test.
		statuses_before_completed = [
			"enrolling_by_invitation",  # rank 1
			"not_yet_recruiting",  # rank 2
			"active_not_recruiting",  # rank 3
			"suspended",  # rank 4
			"not recruiting",  # rank 5 — WHO's generic status, note the space
			"unknown",  # rank 6
			"available",  # rank 7 -> OTHER
		]
		statuses_after_completed = [
			"terminated",  # rank 9
			"withdrawn",  # rank 10
		]

		def _make(label, i):
			return _make_trial(
				f"Extra {label}",
				f"https://example.com/recruiting-first-extra-{i}",
				self.team,
				self.subject,
				status=label,
				discovery_date=base,
			)

		before = [_make(s, i) for i, s in enumerate(statuses_before_completed)]
		after = [_make(s, i + 100) for i, s in enumerate(statuses_after_completed)]

		# page_size default is 10; ask for all 12 trials in one page.
		response = self.client.get(
			"/trials/?ordering=recruiting_first&page_size=100"
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		ids = self._trial_ids(response)

		expected = (
			[self.recruiting.trial_id]
			+ [t.trial_id for t in before]
			+ [self.completed.trial_id]
			+ [t.trial_id for t in after]
			+ [self.no_status.trial_id]
		)
		self.assertEqual(ids, expected)

	def test_composes_with_recruitment_status_normalized_filter(self):
		response = self.client.get(
			"/trials/",
			{
				"subject_id": self.subject.id,
				"recruitment_status_normalized": "recruiting",
				"ordering": "recruiting_first",
			},
		)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(self._trial_ids(response), [self.recruiting.trial_id])

	def test_query_count_unchanged_for_default_list(self):
		"""The recruiting_first annotation is one extra CASE expression in the
		SELECT, not a join — it must not add queries to a plain list request."""
		# Warm-up request (uncaptured): django.contrib.sites' CurrentSiteMiddleware
		# calls Site.objects.get_current(), cached in a process-global SITE_CACHE
		# (not per-test) — so whichever of the two captured requests below ran
		# first in the process would otherwise pay that one-time query, an
		# off-by-one that depends on test execution order. See
		# api/tests/test_trial_site_api.py for the same fix.
		self.client.get("/trials/")

		with CaptureQueriesContext(connection) as ctx:
			response = self.client.get("/trials/")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		baseline_count = len(ctx.captured_queries)

		with CaptureQueriesContext(connection) as ctx:
			response = self.client.get("/trials/?ordering=recruiting_first")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		self.assertEqual(len(ctx.captured_queries), baseline_count)

	def test_existing_orderings_unaffected(self):
		response = self.client.get("/trials/?ordering=-discovery_date")
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		# Default ordering is unchanged: newest discovery_date first among ties
		# is undetermined by our fixtures (same discovery_date), but the request
		# must still succeed and return all three trials.
		self.assertEqual(len(response.data["results"]), 3)


class RecruitingFirstStablePaginationTest(TestCase):
	"""Many trials share a rank, so recruiting_first alone gives an unstable page
	order — the tiebreaker (-discovery_date) added by TrialViewSet.filter_queryset
	must make ordering deterministic and pagination duplicate-free."""

	def setUp(self):
		self.organization, self.team = _make_org_team(
			"Recruiting First Stable Org", "recruiting-first-stable-org"
		)
		self.subject = Subject.objects.create(
			subject_name="Recruiting First Stable Subject",
			subject_slug="recruiting-first-stable-subject",
			team=self.team,
		)

		# 5 "recruiting" trials (same rank) with distinct discovery_date values so
		# the tiebreaker gives a single deterministic order.
		self.trials = [
			_make_trial(
				f"Recruiting {i}",
				f"https://example.com/recruiting-first-stable-{i}",
				self.team,
				self.subject,
				status="Recruiting",
				discovery_date=datetime(2026, 1, i + 1, tzinfo=dt_timezone.utc),
			)
			for i in range(5)
		]
		self.client = APIClient()

	def _trial_ids(self, params):
		response = self.client.get("/trials/", params)
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		return [row["trial_id"] for row in response.data["results"]]

	def test_same_status_trials_return_in_deterministic_order_across_requests(self):
		first = self._trial_ids({"ordering": "recruiting_first", "page_size": 100})
		second = self._trial_ids({"ordering": "recruiting_first", "page_size": 100})
		self.assertEqual(first, second)
		# Newest discovery_date first, per the -discovery_date tiebreaker.
		expected = [t.trial_id for t in reversed(self.trials)]
		self.assertEqual(first, expected)

	def test_pagination_has_no_duplicates_across_pages(self):
		page1 = self._trial_ids(
			{"ordering": "recruiting_first", "page_size": 3, "page": 1}
		)
		page2 = self._trial_ids(
			{"ordering": "recruiting_first", "page_size": 3, "page": 2}
		)
		self.assertEqual(len(page1), 3)
		self.assertEqual(len(page2), 2)
		self.assertEqual(set(page1) & set(page2), set())
		self.assertEqual(set(page1) | set(page2), {t.trial_id for t in self.trials})
