"""
Tests for the dedicated ``GET /articles/stats/`` endpoint.

Mirrors the /trials/stats/ suite for articles (both are built on
``CachedStatsActionMixin``). Locks in:

  - the default /articles/ list response has no ``stats`` key and never
    runs the stats aggregation
  - /articles/stats/ totals: total, by_access (NULL access folded into
    "unknown"), relevant, retracted, missing_doi
  - the relevant count uses the list's ?relevant=true semantics (live
    manual+ML logic, subject-scoped when subject_id is present) — NOT the
    denormalized any-subject Articles.relevant flag
  - filters (e.g. team_id) scope the stats like the equivalent list request
  - by_subject breakdown, including that hidden-org subjects on visible
    articles never leak into it
  - server-side caching: identical second request served from cache without
    touching the articles table; different params and different visible-org
    contexts never share a cache entry
  - routing: /articles/stats/ resolves to the action and /articles/<pk>/
    detail lookups still work

Run with:
    docker exec gregory python manage.py test api.tests.test_articles_stats
"""

from datetime import timedelta

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now
from organizations.models import Organization
from rest_framework.test import APIClient

from api.models import APIAccessScheme
from gregory.models import (
	Articles,
	ArticleSubjectRelevance,
	MLPredictions,
	OrganizationApiSettings,
	Subject,
	Team,
)


def _make_org_team(name, slug, public=True):
	org = Organization.objects.create(name=name, slug=slug)
	OrganizationApiSettings.objects.filter(organization=org).update(
		make_api_public=public
	)
	team = Team.objects.create(organization=org, name=name, slug=slug)
	return org, team


def _make_subject(team, name, slug):
	return Subject.objects.create(team=team, subject_name=name, subject_slug=slug)


def _make_article(
	title,
	link,
	teams,
	subjects=(),
	access=None,
	doi=None,
	relevant=False,
	retracted=False,
):
	article = Articles.objects.create(
		title=title,
		link=link,
		access=access,
		doi=doi,
		relevant=relevant,
		retracted=retracted,
		kind="science paper",
	)
	for team in teams:
		article.teams.add(team)
	for subject in subjects:
		article.subjects.add(subject)
	return article


def _make_api_scheme(org, name):
	return APIAccessScheme.objects.create(
		client_name=name,
		client_contacts=f"{name}@example.com",
		organization=org,
		ip_addresses="",
		begin_date=now() - timedelta(days=1),
		end_date=now() + timedelta(days=30),
	)


def _access_aggregation_ran(captured_queries):
	"""True when the GROUP BY access aggregation appears in *captured_queries*."""
	return any(
		"GROUP BY" in q["sql"] and "access" in q["sql"]
		for q in captured_queries
	)


class ArticleStatsBase(TestCase):
	def setUp(self):
		# The DB cache persists across tests within a worker — clear it so
		# cached stats from a previous test can't leak into this one.
		cache.clear()

		self.org, self.team = _make_org_team("A-Stats Org", "a-stats-org")
		self.other_org, self.other_team = _make_org_team(
			"A-Other Stats Org", "a-other-stats-org"
		)
		self.subject = _make_subject(self.team, "A-Stats Subject", "a-stats-subject")

		# a1: open access, relevant (manually reviewed for the subject), has
		# DOI. The stats `relevant` count uses the live per-subject logic —
		# the denormalized flag alone is not enough to count as relevant.
		self.a1 = _make_article(
			"A1",
			"https://article.example.com/1",
			[self.team],
			[self.subject],
			access="open",
			doi="10.1000/stats-a1",
			relevant=True,
		)
		ArticleSubjectRelevance.objects.create(
			article=self.a1, subject=self.subject, is_relevant=True
		)
		# a2: restricted, retracted, has DOI
		self.a2 = _make_article(
			"A2",
			"https://article.example.com/2",
			[self.team],
			[self.subject],
			access="restricted",
			doi="10.1000/stats-a2",
			retracted=True,
		)
		# a3: explicit 'unknown' access, empty-string DOI (missing)
		self.a3 = _make_article(
			"A3",
			"https://article.example.com/3",
			[self.team],
			access="unknown",
			doi="",
		)
		# a4: NULL access (never checked) and NULL DOI — must fold into
		# "unknown" / count as missing_doi. Lives in the other org's team.
		self.a4 = _make_article(
			"A4",
			"https://article.example.com/4",
			[self.other_team],
			access=None,
			doi=None,
		)

		self.client = APIClient()


class ArticleListNoStatsTest(ArticleStatsBase):
	"""The paginated list has no stats key and runs no stats aggregation."""

	def test_default_list_has_no_stats_key(self):
		resp = self.client.get("/articles/")
		self.assertEqual(resp.status_code, 200)
		self.assertNotIn("stats", resp.data)

	def test_default_list_does_not_run_stats_aggregation_query(self):
		with CaptureQueriesContext(connection) as ctx:
			resp = self.client.get("/articles/")
		self.assertEqual(resp.status_code, 200)
		self.assertFalse(
			_access_aggregation_ran(ctx.captured_queries),
			msg="The stats GROUP BY aggregation must not run on plain list requests",
		)


class ArticleStatsEndpointTest(ArticleStatsBase):
	"""Totals, by_access folding, flag counts, and filter scoping."""

	def test_stats_endpoint_returns_correct_totals(self):
		resp = self.client.get("/articles/stats/")
		self.assertEqual(resp.status_code, 200)
		stats = resp.data
		self.assertEqual(stats["total"], 4)
		self.assertEqual(stats["relevant"], 1)
		self.assertEqual(stats["retracted"], 1)
		self.assertEqual(stats["missing_doi"], 2)  # a3 ('') + a4 (NULL)

	def test_by_access_folds_null_into_unknown(self):
		resp = self.client.get("/articles/stats/")
		self.assertEqual(resp.status_code, 200)
		by_access = resp.data["by_access"]
		self.assertEqual(by_access["open"], 1)
		self.assertEqual(by_access["restricted"], 1)
		# a3 (explicit 'unknown') + a4 (NULL) — consumers must not see the
		# internal NULL/'unknown' split.
		self.assertEqual(by_access["unknown"], 2)
		self.assertEqual(set(by_access.keys()), {"open", "restricted", "unknown"})

	def test_stats_with_team_filter_scopes_totals(self):
		resp = self.client.get("/articles/stats/", {"team_id": self.team.id})
		self.assertEqual(resp.status_code, 200)
		stats = resp.data
		self.assertEqual(stats["total"], 3)  # a1, a2, a3
		self.assertEqual(stats["by_access"]["unknown"], 1)  # a3 only
		self.assertEqual(stats["relevant"], 1)
		self.assertEqual(stats["retracted"], 1)
		self.assertEqual(stats["missing_doi"], 1)  # a3

	def test_article_visible_under_two_teams_is_not_double_counted(self):
		self.a1.teams.add(self.other_team)
		resp = self.client.get("/articles/stats/")
		self.assertEqual(resp.status_code, 200)
		stats = resp.data
		self.assertEqual(stats["total"], 4)  # still 4 distinct articles
		self.assertEqual(stats["by_access"]["open"], 1)
		self.assertEqual(stats["relevant"], 1)


class ArticleStatsBySubjectTest(ArticleStatsBase):
	"""The by_subject breakdown, including hidden-org subject stripping."""

	def test_by_subject_counts_distinct_articles(self):
		other_subject = _make_subject(
			self.other_team, "A-Other Subject", "a-other-subject"
		)
		self.a3.subjects.add(self.subject)
		self.a4.subjects.add(other_subject)

		resp = self.client.get("/articles/stats/")
		self.assertEqual(resp.status_code, 200)
		by_subject = {row["subject_id"]: row for row in resp.data["by_subject"]}
		self.assertEqual(by_subject[self.subject.id]["count"], 3)  # a1, a2, a3
		self.assertEqual(
			by_subject[self.subject.id]["subject_name"], "A-Stats Subject"
		)
		self.assertEqual(by_subject[other_subject.id]["count"], 1)  # a4

	def test_by_subject_scoped_by_filter(self):
		other_subject = _make_subject(
			self.other_team, "A-Other Subject", "a-other-subject"
		)
		self.a4.subjects.add(other_subject)

		resp = self.client.get("/articles/stats/", {"team_id": self.team.id})
		self.assertEqual(resp.status_code, 200)
		subject_ids = [row["subject_id"] for row in resp.data["by_subject"]]
		self.assertIn(self.subject.id, subject_ids)
		self.assertNotIn(other_subject.id, subject_ids)

	def test_hidden_org_subject_never_appears_in_by_subject(self):
		# A visible (public-org) article tagged with a subject belonging to
		# a NON-visible org: the subject must not leak into by_subject, even
		# though the article itself is counted.
		hidden_org, hidden_team = _make_org_team(
			"A-Hidden Org", "a-hidden-org", public=False
		)
		hidden_subject = _make_subject(
			hidden_team, "A-Hidden Subject", "a-hidden-subject"
		)
		self.a1.subjects.add(hidden_subject)

		resp = self.client.get("/articles/stats/")
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["total"], 4)  # a1 still counted
		subject_ids = [row["subject_id"] for row in resp.data["by_subject"]]
		self.assertIn(self.subject.id, subject_ids)
		self.assertNotIn(hidden_subject.id, subject_ids)


class ArticleStatsCachingTest(ArticleStatsBase):
	"""Server-side caching: hits, param separation, and tenant isolation."""

	def test_second_identical_request_served_from_cache(self):
		first = self.client.get("/articles/stats/")
		self.assertEqual(first.status_code, 200)

		with CaptureQueriesContext(connection) as ctx:
			second = self.client.get("/articles/stats/")
		self.assertEqual(second.status_code, 200)
		self.assertEqual(second.data, first.data)
		# The DB cache read itself is a SQL query (gregory_cache table), so
		# assert the stats work specifically did not re-run: no aggregation
		# and no query against the articles table at all.
		self.assertFalse(
			_access_aggregation_ran(ctx.captured_queries),
			msg="Cached stats request re-ran the GROUP BY aggregation",
		)
		self.assertFalse(
			any('FROM "articles"' in q["sql"] for q in ctx.captured_queries),
			msg="Cached stats request queried the articles table",
		)

	def test_different_query_params_do_not_share_cache_entry(self):
		all_stats = self.client.get("/articles/stats/")
		team_stats = self.client.get("/articles/stats/", {"team_id": self.team.id})
		other_stats = self.client.get(
			"/articles/stats/", {"team_id": self.other_team.id}
		)
		self.assertEqual(all_stats.data["total"], 4)
		self.assertEqual(team_stats.data["total"], 3)
		self.assertEqual(other_stats.data["total"], 1)

	def test_cache_is_isolated_per_visible_org_context(self):
		# A private org with its own article: anonymous callers cannot see
		# it, an API key bound to the org can. If the cache key ignored the
		# caller's visible orgs, whichever request ran first would leak its
		# stats to the other.
		priv_org, priv_team = _make_org_team(
			"A-Private Stats Org", "a-private-stats-org", public=False
		)
		priv_subject = _make_subject(
			priv_team, "A-Private Subject", "a-private-subject"
		)
		priv_article = _make_article(
			"Private A",
			"https://article.example.com/priv",
			[priv_team],
			[priv_subject],
			access="open",
			doi="10.1000/stats-priv",
			relevant=True,
		)
		ArticleSubjectRelevance.objects.create(
			article=priv_article, subject=priv_subject, is_relevant=True
		)
		scheme = _make_api_scheme(priv_org, "a-stats-key")

		anon = APIClient()
		anon_resp = anon.get("/articles/stats/")
		self.assertEqual(anon_resp.status_code, 200)
		# Anonymous sees only the two public orgs' 4 articles.
		self.assertEqual(anon_resp.data["total"], 4)

		keyed = APIClient()
		keyed.credentials(HTTP_AUTHORIZATION=scheme.api_key)
		keyed_resp = keyed.get("/articles/stats/")
		self.assertEqual(keyed_resp.status_code, 200)
		# The org-scoped caller sees only its own org's single article — if
		# it got the anonymous caller's cached payload this would be 4.
		self.assertEqual(keyed_resp.data["total"], 1)
		self.assertEqual(keyed_resp.data["relevant"], 1)

		# And the reverse: a fresh anonymous request after the keyed one must
		# not pick up the keyed caller's entry.
		anon_again = anon.get("/articles/stats/")
		self.assertEqual(anon_again.data["total"], 4)


class ArticleStatsSubjectScopedRelevantTest(TestCase):
	"""The `relevant` count must be subject-strict when subject_id is given.

	An article tagged with subjects N and M but only relevant for M must not
	land in N's relevant bucket — the stats must mean the same thing as
	`/articles/?relevant=true&subject_id=N`, not "relevant for any subject"
	(the denormalized Articles.relevant flag).
	"""

	def setUp(self):
		cache.clear()
		self.org, self.team = _make_org_team(
			"A-Scoped Org", "a-scoped-stats-org"
		)
		self.subject_n = _make_subject(self.team, "Subject N", "a-scoped-subject-n")
		self.subject_m = _make_subject(self.team, "Subject M", "a-scoped-subject-m")

		# In both subjects, manually relevant ONLY for M.
		self.article = _make_article(
			"Scoped A1",
			"https://article.example.com/scoped-1",
			[self.team],
			[self.subject_n, self.subject_m],
			access="open",
			doi="10.1000/stats-scoped-1",
		)
		ArticleSubjectRelevance.objects.create(
			article=self.article, subject=self.subject_m, is_relevant=True
		)

		self.client = APIClient()

	def _stats(self, **params):
		resp = self.client.get("/articles/stats/", params)
		self.assertEqual(resp.status_code, 200)
		return resp.data

	def _list_relevant_count(self, **params):
		resp = self.client.get("/articles/", {"relevant": "true", **params})
		self.assertEqual(resp.status_code, 200)
		return resp.data["count"]

	def test_relevant_is_scoped_to_subject_param(self):
		stats_n = self._stats(subject_id=self.subject_n.id)
		# The article IS in subject N (total counts it) but is not relevant
		# FOR subject N.
		self.assertEqual(stats_n["total"], 1)
		self.assertEqual(stats_n["relevant"], 0)

		stats_m = self._stats(subject_id=self.subject_m.id)
		self.assertEqual(stats_m["total"], 1)
		self.assertEqual(stats_m["relevant"], 1)

	def test_unscoped_relevant_counts_any_subject_once(self):
		stats = self._stats()
		self.assertEqual(stats["total"], 1)
		self.assertEqual(stats["relevant"], 1)

	def test_stats_relevant_matches_list_relevant_count(self):
		"""The contract: stats counts == what the equivalent list returns."""
		for params in (
			{},
			{"subject_id": self.subject_n.id},
			{"subject_id": self.subject_m.id},
		):
			self.assertEqual(
				self._stats(**params)["relevant"],
				self._list_relevant_count(**params),
				f"stats/list relevant mismatch for params {params}",
			)

	def test_ml_consensus_relevant_is_subject_scoped(self):
		"""ML-driven relevance is also per subject: a consensus prediction
		for subject N must not make the article relevant for subject M."""
		self.subject_n.auto_predict = True
		self.subject_n.save()
		ml_article = _make_article(
			"Scoped ML A2",
			"https://article.example.com/scoped-2",
			[self.team],
			[self.subject_n, self.subject_m],
			access="open",
			doi="10.1000/stats-scoped-2",
		)
		MLPredictions.objects.create(
			article=ml_article,
			subject=self.subject_n,
			algorithm="pubmed_bert",
			model_version="test_v1",
			probability_score=0.95,
			predicted_relevant=True,
		)

		stats_n = self._stats(subject_id=self.subject_n.id)
		self.assertEqual(stats_n["relevant"], 1)  # ml_article via consensus

		stats_m = self._stats(subject_id=self.subject_m.id)
		# Only the manually-relevant article; the ML prediction belongs to N.
		self.assertEqual(stats_m["relevant"], 1)
		self.assertEqual(
			stats_m["relevant"], self._list_relevant_count(subject_id=self.subject_m.id)
		)


class ArticleStatsRoutingTest(ArticleStatsBase):
	"""/articles/stats/ resolves to the action; numeric detail lookups still work."""

	def test_stats_route_resolves_to_action(self):
		resp = self.client.get("/articles/stats/")
		self.assertEqual(resp.status_code, 200)
		self.assertIn("total", resp.data)
		self.assertIn("by_access", resp.data)
		self.assertIn("by_subject", resp.data)
		self.assertNotIn("results", resp.data)

	def test_numeric_detail_route_still_works(self):
		resp = self.client.get(f"/articles/{self.a1.article_id}/")
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["article_id"], self.a1.article_id)
