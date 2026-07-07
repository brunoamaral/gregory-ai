"""
Phase 0 regression tests for the /articles/?all_results=true 504 fix.

Covers:
  - ArticleViewSet no longer issues one query per article per related field
    (locks in the prefetch_related added to ArticleViewSet.queryset).
  - The bulk-export scoped throttle only engages when all_results=true is
    present; plain paginated requests are never throttled.

Run with:
    docker exec gregory python manage.py test api.tests.test_article_query_efficiency
"""

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from organizations.models import Organization
from rest_framework.test import APIClient

from gregory.models import (
	Articles,
	ArticleSubjectRelevance,
	ArticleTrialReference,
	Authors,
	OrganizationApiSettings,
	Sources,
	Team,
	TeamCategory,
	Subject,
	Trials,
)


def _build_articles(n, suffix):
	"""Create *n* articles, each wired to one of every related field the
	serializer touches (authors, teams, subjects, sources, team_categories,
	article_subject_relevances, trial_references) so an un-prefetched
	relation shows up as O(n) queries instead of O(1).
	"""
	org = Organization.objects.create(name=f"Org {suffix}", slug=f"org-{suffix}")
	# Anonymous requests only see orgs flagged public (gregory/visibility.py).
	OrganizationApiSettings.objects.filter(organization=org).update(
		make_api_public=True
	)
	team = Team.objects.create(
		organization=org, name=f"Team {suffix}", slug=f"team-{suffix}"
	)
	subject = Subject.objects.create(
		team=team, subject_name=f"Subject {suffix}", subject_slug=f"subject-{suffix}"
	)
	source = Sources.objects.create(
		name=f"Source {suffix}", source_for="science paper"
	)
	category = TeamCategory.objects.create(
		team=team, category_name=f"Category {suffix}", category_slug=f"category-{suffix}"
	)
	category.subjects.add(subject)
	trial = Trials.objects.create(title=f"Trial {suffix}", link=f"https://trials.example.com/{suffix}")

	for i in range(n):
		article = Articles.objects.create(
			title=f"Article {suffix}-{i}",
			link=f"https://example.com/{suffix}-{i}",
			kind="science paper",
		)
		article.teams.add(team)
		article.subjects.add(subject)
		article.sources.add(source)
		article.team_categories.add(category)

		author = Authors.objects.create(
			given_name="Given", family_name=f"Family{suffix}{i}"
		)
		article.authors.add(author)

		ArticleSubjectRelevance.objects.create(
			article=article, subject=subject, is_relevant=True
		)
		ArticleTrialReference.objects.create(
			article=article,
			trial=trial,
			identifier_type="nct_id",
			identifier_value=f"NCT{suffix}{i}",
		)


class TestArticleListQueryEfficiency(TestCase):
	"""Locks in the ArticleViewSet prefetch_related from Phase 0.1."""

	def test_query_count_does_not_scale_with_article_count(self):
		client = APIClient()

		# Warm Django's sites-framework cache (Site.objects.get_current() is
		# process-global and memoised after its first call) so it doesn't show
		# up as a one-off extra query on whichever capture runs first.
		from django.contrib.sites.models import Site

		Site.objects.get_current()

		_build_articles(3, "a")
		with CaptureQueriesContext(connection) as small:
			response = client.get("/articles/", {"page_size": 100})
		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.data["results"]), 3)

		Articles.objects.all().delete()
		_build_articles(9, "b")
		with CaptureQueriesContext(connection) as large:
			response = client.get("/articles/", {"page_size": 100})
		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.data["results"]), 9)

		# If any relation regresses to one-query-per-article, the 9-article
		# request issues more queries than the 3-article one. Allow a small
		# constant slack rather than exact equality: one-time caches (sites
		# framework, content types, etc.) can shift the count by a query or
		# two between runs without indicating a real N+1 regression.
		slack = 2
		self.assertLessEqual(
			len(large.captured_queries),
			len(small.captured_queries) + slack,
			msg=(
				"Query count scaled with article count "
				f"({len(small.captured_queries)} for 3 articles vs "
				f"{len(large.captured_queries)} for 9 articles) — an N+1 "
				"regression was reintroduced in ArticleViewSet/ArticleSerializer."
			),
		)


class TestBulkExportThrottle(TestCase):
	"""Phase 0.3: scoped throttle only fires on the all_results bypass path.

	Exercises the real ``bulk_export`` rate from settings rather than
	overriding it: DRF's ``SimpleRateThrottle.THROTTLE_RATES`` is a class
	attribute snapshotted from ``api_settings`` at import time, so
	``override_settings(REST_FRAMEWORK=...)`` does not actually change the
	rate a running throttle instance enforces.
	"""

	def setUp(self):
		cache.clear()
		from rest_framework.throttling import ScopedRateThrottle

		throttle = ScopedRateThrottle()
		throttle.scope = "bulk_export"
		throttle.rate = throttle.get_rate()
		self.limit, _ = throttle.parse_rate(throttle.rate)

	def test_all_results_requests_are_throttled_beyond_the_limit(self):
		client = APIClient()

		for _ in range(self.limit):
			response = client.get("/articles/", {"all_results": "true"})
			self.assertEqual(response.status_code, 200)

		response = client.get("/articles/", {"all_results": "true"})
		self.assertEqual(response.status_code, 429)

	def test_paginated_requests_are_never_throttled(self):
		client = APIClient()

		# Same client, well beyond the bulk_export limit — must all succeed
		# because none of these requests set all_results=true.
		for _ in range(self.limit + 2):
			response = client.get("/articles/", {"page_size": 10})
			self.assertEqual(response.status_code, 200)
