"""
Tests for HOUSE-LOAD-SPIKE-P2-QUERY-COST.md item 3: CachedCountMixin caches
the paginator's COUNT(*) in the shared DB cache, keyed on request path +
visible org ids + filter params (ordering/format/page/page_size/all_results
excluded — they don't change the count).

Run with:
    docker exec gregory python manage.py test api.tests.test_paginator_count_cache
"""

from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from api.pagination import CappedPageNumberPagination

from gregory.models import Articles, OrganizationApiSettings, Team, Trials
from organizations.models import Organization


class CountCacheSmokeTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Smoke Org", slug="smoke-org")
		OrganizationApiSettings.objects.filter(organization=self.org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			organization=self.org, name="Smoke Team", slug="smoke-team"
		)
		for i in range(3):
			a = Articles.objects.create(
				title=f"Smoke {i}", link=f"https://ex.com/smoke-{i}"
			)
			a.teams.add(self.team)
		for i in range(2):
			t = Trials.objects.create(
				title=f"Smoke Trial {i}", link=f"https://ex.com/smoke-trial-{i}"
			)
			t.teams.add(self.team)

		self.client = APIClient()

	def _real_count_queries(self, captured):
		return [
			q["sql"]
			for q in captured
			if "COUNT(*) FROM (SELECT" in q["sql"]
		]

	def test_second_request_skips_real_count_query(self):
		"""Doesn't assert anything about *how* the cache is reached (a DB
		round-trip vs. an in-process lookup) — CACHES swaps to LocMemCache
		under pytest/CI (admin/settings_test.py) precisely to avoid Postgres
		round-trips, while local `manage.py test` runs against the real
		DatabaseCache. Only the backend-agnostic guarantee matters here: the
		real, expensive count query must not run twice."""
		r1 = self.client.get("/articles/")
		self.assertEqual(r1.status_code, 200)

		with CaptureQueriesContext(connection) as ctx:
			r2 = self.client.get("/articles/")
		self.assertEqual(r2.status_code, 200)
		self.assertEqual(r1.data["count"], r2.data["count"])
		self.assertEqual(
			self._real_count_queries(ctx.captured_queries),
			[],
			"real count query ran again on a warm cache",
		)

	def test_authors_endpoint_also_caches_count(self):
		"""CappedPageNumberPagination (GET /authors/) mixes in
		CachedCountMixin too, not just FlexiblePagination."""
		r1 = self.client.get("/authors/")
		self.assertEqual(r1.status_code, 200)

		with CaptureQueriesContext(connection) as ctx:
			r2 = self.client.get("/authors/")
		self.assertEqual(r2.status_code, 200)
		self.assertEqual(r1.data["count"], r2.data["count"])
		self.assertEqual(self._real_count_queries(ctx.captured_queries), [])

	def test_different_filters_do_not_share_a_cache_entry(self):
		"""?team_id=X and no filter must not collide on the same count."""
		other_team = Team.objects.create(
			organization=self.org, name="Other Smoke Team", slug="other-smoke-team"
		)
		self.client.get("/articles/")  # warm the unfiltered count
		r_filtered = self.client.get("/articles/", {"team_id": other_team.id})
		self.assertEqual(r_filtered.data["count"], 0)

		r_unfiltered = self.client.get("/articles/")
		self.assertEqual(r_unfiltered.data["count"], 3)

	def test_articles_and_trials_counts_do_not_collide(self):
		"""Same visible orgs, same (empty) filters, different resource — the
		cache key must include the request path or these would collide."""
		r_articles = self.client.get("/articles/")
		r_trials = self.client.get("/trials/")
		self.assertEqual(r_articles.data["count"], 3)
		self.assertEqual(r_trials.data["count"], 2)

	def test_ordering_and_format_do_not_fragment_the_cache(self):
		"""ordering/format are in the ignored-params set for the count key
		(they don't change the count, only the stats key)."""
		self.client.get("/articles/", {"ordering": "title"})
		with CaptureQueriesContext(connection) as ctx:
			r2 = self.client.get("/articles/", {"ordering": "-title"})
		self.assertEqual(r2.status_code, 200)
		self.assertEqual(
			self._real_count_queries(ctx.captured_queries),
			[],
			"different ordering should reuse the same count cache entry",
		)

	def test_authors_sort_by_and_order_do_not_fragment_the_cache(self):
		"""/authors/ doesn't use DRF's `ordering` param — it has its own
		sort_by/order (AuthorsViewSet.get_queryset), which is also excluded
		from the count key since it never changes the row count, only the
		sort. Without this, sort_by/order would still be in the key (unlike
		ordering/format) and every sort_by×order combination would miss the
		cache."""
		self.client.get("/authors/", {"sort_by": "article_count", "order": "desc"})
		with CaptureQueriesContext(connection) as ctx:
			r2 = self.client.get("/authors/", {"sort_by": "full_name", "order": "asc"})
		self.assertEqual(r2.status_code, 200)
		self.assertEqual(
			self._real_count_queries(ctx.captured_queries),
			[],
			"different sort_by/order should reuse the same count cache entry",
		)


class CountCacheSubjectScopeTests(TestCase):
	"""Site-scoped API visibility, Phase 4: the subject scope is part of the
	cache key.

	The key hashes the org scope AND the subject scope during the transition.
	Call sites convert file by file, so some responses are still org-scoped
	while others are subject-scoped; keying on only one of them could serve a
	count computed under one rule to a caller scoped by the other. The org
	half retires in Phase 6 along with visible_org_ids.
	"""

	def setUp(self):
		self.factory = RequestFactory()

	def _key(self, org_ids, subject_ids):
		request = self.factory.get("/articles/")
		request.visible_org_ids = org_ids
		request.visible_subject_ids = subject_ids
		# DRF's query_params is a thin wrapper over GET on a plain request.
		request.query_params = request.GET
		return CappedPageNumberPagination()._count_cache_key(request)

	def test_different_subject_scopes_do_not_share_a_cache_entry(self):
		"""The property the whole key exists for. Two callers whose org scope
		happens to match but whose subject scope differs must not collide."""
		same_orgs = {1}
		self.assertNotEqual(
			self._key(same_orgs, {1, 2}),
			self._key(same_orgs, {3, 4}),
		)

	def test_different_org_scopes_still_do_not_share_a_cache_entry(self):
		"""The pre-existing property, unchanged by adding subjects."""
		same_subjects = {1}
		self.assertNotEqual(
			self._key({1}, same_subjects),
			self._key({2}, same_subjects),
		)

	def test_identical_scopes_share_a_cache_entry(self):
		"""Otherwise the cache never hits and the whole mechanism is dead
		weight -- worth pinning alongside the isolation cases."""
		self.assertEqual(
			self._key({1, 2}, {3, 4}),
			self._key({2, 1}, {4, 3}),
		)

	def test_a_missing_subject_scope_is_distinct_from_an_empty_one(self):
		"""None means the middleware never ran (management command, test
		bypassing middleware); set() means it ran and the caller can see
		nothing. Collapsing them would let an unscoped internal caller share
		a cache entry with a caller scoped to nothing."""
		self.assertNotEqual(
			self._key({1}, None),
			self._key({1}, set()),
		)
