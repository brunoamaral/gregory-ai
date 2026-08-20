"""
Tests for HOUSE-LOAD-SPIKE-P2-QUERY-COST.md item 3: CachedCountMixin caches
the paginator's COUNT(*) in the shared DB cache, keyed on request path +
visible org ids + filter params (ordering/format/page/page_size/all_results
excluded — they don't change the count).

Run with:
    docker exec gregory python manage.py test api.tests.test_paginator_count_cache
"""

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

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
