"""
Tests for the dedicated ``GET /trials/stats/`` endpoint.

Before this change, ``TrialViewSet.list()`` unconditionally re-ran
``filter_queryset(self.get_queryset())`` and executed a GROUP BY
recruitment_status aggregation over the full filtered queryset on *every*
paginated list request. The stats now live on their own routed action
(``CachedStatsActionMixin``). This suite locks in:

  - the default /trials/ list response has no ``stats`` key and never runs
    the aggregation query
  - /trials/stats/ returns correct totals and honours the same filters
    (e.g. team_id) as the list endpoint
  - Count(distinct=True): a trial visible under two teams is counted once
  - by_subject breakdown, including that hidden-org subjects on visible
    trials never leak into it
  - server-side caching: an identical second request is served from the
    shared DB cache without re-running the aggregation; different query
    params and different visible-org contexts never share a cache entry
  - routing: /trials/stats/ resolves to the action and /trials/<pk>/
    detail lookups still work

Run with:
    docker exec gregory python manage.py test api.tests.test_trials_stats
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
from gregory.models import OrganizationApiSettings, Subject, Team, Trials


def _make_org_team(name, slug, public=True):
	org = Organization.objects.create(name=name, slug=slug)
	OrganizationApiSettings.objects.filter(organization=org).update(
		make_api_public=public
	)
	team = Team.objects.create(organization=org, name=name, slug=slug)
	return org, team


def _make_subject(team, name, slug):
	return Subject.objects.create(team=team, subject_name=name, subject_slug=slug)


def _make_trial(title, link, status, teams, subjects=()):
	trial = Trials.objects.create(title=title, link=link, recruitment_status=status)
	for team in teams:
		trial.teams.add(team)
	for subject in subjects:
		trial.subjects.add(subject)
	return trial


def _make_api_scheme(org, name):
	return APIAccessScheme.objects.create(
		client_name=name,
		client_contacts=f"{name}@example.com",
		organization=org,
		ip_addresses="",
		begin_date=now() - timedelta(days=1),
		end_date=now() + timedelta(days=30),
	)


def _stats_aggregation_ran(captured_queries):
	"""True when the GROUP BY recruitment_status aggregation appears in *captured_queries*."""
	return any(
		"GROUP BY" in q["sql"] and "recruitment_status" in q["sql"]
		for q in captured_queries
	)


class TrialStatsBase(TestCase):
	def setUp(self):
		# The DB cache persists across tests within a worker — clear it so
		# cached stats from a previous test can't leak into this one.
		cache.clear()

		self.org, self.team = _make_org_team("Stats Org", "stats-org")
		self.other_org, self.other_team = _make_org_team(
			"Other Stats Org", "other-stats-org"
		)
		self.subject = _make_subject(self.team, "Stats Subject", "stats-subject")

		self.t1 = _make_trial(
			"T1",
			"https://trial.example.com/1",
			"Recruiting",
			[self.team],
			[self.subject],
		)
		self.t2 = _make_trial(
			"T2",
			"https://trial.example.com/2",
			"Recruiting",
			[self.team],
			[self.subject],
		)
		self.t3 = _make_trial(
			"T3", "https://trial.example.com/3", "COMPLETED", [self.team]
		)
		self.t4 = _make_trial(
			"T4", "https://trial.example.com/4", "TERMINATED", [self.other_team]
		)

		self.client = APIClient()


class TrialListNoStatsTest(TrialStatsBase):
	"""The paginated list no longer computes or embeds stats."""

	def test_default_list_has_no_stats_key(self):
		resp = self.client.get("/trials/")
		self.assertEqual(resp.status_code, 200)
		self.assertNotIn("stats", resp.data)

	def test_list_ignores_legacy_include_stats_param(self):
		# The transitional include_stats=true opt-in never shipped; the list
		# must not resurrect stats for any parameter value.
		for params in (
			{"include_stats": "true"},
			{"include_stats": "1"},
			{"include_stats": "yes"},
		):
			resp = self.client.get("/trials/", params)
			self.assertEqual(resp.status_code, 200)
			self.assertNotIn(
				"stats", resp.data, msg=f"params={params!r} must not include stats"
			)

	def test_default_list_does_not_run_stats_aggregation_query(self):
		with CaptureQueriesContext(connection) as ctx:
			resp = self.client.get("/trials/")
		self.assertEqual(resp.status_code, 200)
		self.assertFalse(
			_stats_aggregation_ran(ctx.captured_queries),
			msg="The stats GROUP BY aggregation must not run on plain list requests",
		)


class TrialStatsEndpointTest(TrialStatsBase):
	"""Totals, filter scoping, and the distinct-count fix."""

	def test_stats_endpoint_returns_correct_totals(self):
		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		stats = resp.data
		self.assertEqual(stats["total"], 4)
		self.assertEqual(stats["recruiting"], 2)
		self.assertEqual(stats["completed"], 1)
		self.assertEqual(stats["terminated"], 1)

	def test_stats_endpoint_runs_aggregation_query(self):
		with CaptureQueriesContext(connection) as ctx:
			resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		self.assertTrue(
			_stats_aggregation_ran(ctx.captured_queries),
			msg="Expected a GROUP BY recruitment_status aggregation query",
		)

	def test_stats_with_team_filter_scopes_totals(self):
		resp = self.client.get("/trials/stats/", {"team_id": self.team.id})
		self.assertEqual(resp.status_code, 200)
		stats = resp.data
		self.assertEqual(stats["total"], 3)
		self.assertEqual(stats["recruiting"], 2)
		self.assertEqual(stats["completed"], 1)
		self.assertEqual(stats["terminated"], 0)

	def test_stats_with_other_team_filter_scopes_totals(self):
		resp = self.client.get("/trials/stats/", {"team_id": self.other_team.id})
		self.assertEqual(resp.status_code, 200)
		stats = resp.data
		self.assertEqual(stats["total"], 1)
		self.assertEqual(stats["terminated"], 1)
		self.assertEqual(stats["recruiting"], 0)

	def test_stats_with_status_filter_scopes_totals(self):
		resp = self.client.get("/trials/stats/", {"status": "Recruiting"})
		self.assertEqual(resp.status_code, 200)
		stats = resp.data
		self.assertEqual(stats["total"], 2)
		self.assertEqual(stats["recruiting"], 2)
		self.assertEqual(stats["completed"], 0)

	def test_trial_visible_under_two_teams_is_not_double_counted(self):
		_make_trial(
			"Shared",
			"https://trial.example.com/shared",
			"Recruiting",
			[self.team, self.other_team],
		)
		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		stats = resp.data
		# 4 original trials + 1 shared trial = 5, not 6 (would be 6 if the
		# shared trial were counted once per team via a non-distinct Count).
		self.assertEqual(stats["total"], 5)
		self.assertEqual(stats["recruiting"], 3)


class TrialStatsBySubjectTest(TrialStatsBase):
	"""The by_subject breakdown, including hidden-org subject stripping."""

	def test_by_subject_counts_distinct_trials(self):
		other_subject = _make_subject(
			self.other_team, "Other Subject", "other-subject"
		)
		self.t3.subjects.add(self.subject)
		self.t4.subjects.add(other_subject)

		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		by_subject = {
			row["subject_id"]: row for row in resp.data["by_subject"]
		}
		self.assertEqual(by_subject[self.subject.id]["count"], 3)  # t1, t2, t3
		self.assertEqual(
			by_subject[self.subject.id]["subject_name"], "Stats Subject"
		)
		self.assertEqual(by_subject[other_subject.id]["count"], 1)  # t4

	def test_by_subject_scoped_by_filter(self):
		other_subject = _make_subject(
			self.other_team, "Other Subject", "other-subject"
		)
		self.t4.subjects.add(other_subject)

		resp = self.client.get("/trials/stats/", {"team_id": self.team.id})
		self.assertEqual(resp.status_code, 200)
		subject_ids = [row["subject_id"] for row in resp.data["by_subject"]]
		self.assertIn(self.subject.id, subject_ids)
		self.assertNotIn(other_subject.id, subject_ids)

	def test_hidden_org_subject_never_appears_in_by_subject(self):
		# A visible (public-org) trial tagged with a subject belonging to a
		# NON-visible org: the subject must not leak into by_subject, even
		# though the trial itself is counted.
		hidden_org, hidden_team = _make_org_team(
			"Hidden Org", "hidden-org", public=False
		)
		hidden_subject = _make_subject(
			hidden_team, "Hidden Subject", "hidden-subject"
		)
		self.t1.subjects.add(hidden_subject)

		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["total"], 4)  # t1 still counted
		subject_ids = [row["subject_id"] for row in resp.data["by_subject"]]
		self.assertIn(self.subject.id, subject_ids)
		self.assertNotIn(hidden_subject.id, subject_ids)


class TrialStatsCachingTest(TrialStatsBase):
	"""Server-side caching: hits, param separation, and tenant isolation."""

	def _cache_key_for(self, params):
		from django.test import RequestFactory
		from rest_framework.request import Request

		from api.views import TrialViewSet

		view = TrialViewSet()
		return view._stats_cache_key(
			Request(RequestFactory().get("/trials/stats/", params))
		)

	def test_cache_key_immune_to_param_encoding_collisions(self):
		"""A param VALUE containing '&'/'=' must not collide with a request
		where those characters are real separators. Under a naive 'k=v&k=v'
		concatenation, ?condition=a&intervention=b and
		?condition=a%26intervention%3Db hash to the same key."""
		two_params = self._cache_key_for({"condition": "a", "intervention": "b"})
		one_param = self._cache_key_for({"condition": "a&intervention=b"})
		self.assertNotEqual(two_params, one_param)

	def test_pagination_params_do_not_fragment_cache(self):
		"""page/page_size/all_results never change a stats payload, so they
		are excluded from the cache key."""
		base = self._cache_key_for({"team_id": "1"})
		paged = self._cache_key_for(
			{"team_id": "1", "page": "2", "page_size": "50", "all_results": "true"}
		)
		self.assertEqual(base, paged)

	def test_second_identical_request_served_from_cache(self):
		first = self.client.get("/trials/stats/")
		self.assertEqual(first.status_code, 200)

		with CaptureQueriesContext(connection) as ctx:
			second = self.client.get("/trials/stats/")
		self.assertEqual(second.status_code, 200)
		self.assertEqual(second.data, first.data)
		# The DB cache read itself is a SQL query (gregory_cache table), so
		# we assert specifically that the aggregation did not re-run.
		self.assertFalse(
			_stats_aggregation_ran(ctx.captured_queries),
			msg="Cached stats request re-ran the GROUP BY aggregation",
		)

	def test_different_query_params_do_not_share_cache_entry(self):
		all_stats = self.client.get("/trials/stats/")
		team_stats = self.client.get("/trials/stats/", {"team_id": self.team.id})
		other_stats = self.client.get(
			"/trials/stats/", {"team_id": self.other_team.id}
		)
		self.assertEqual(all_stats.data["total"], 4)
		self.assertEqual(team_stats.data["total"], 3)
		self.assertEqual(other_stats.data["total"], 1)

	def test_cache_is_isolated_per_visible_org_context(self):
		# A private org with its own trial: anonymous callers cannot see it,
		# an API key bound to the org can. If the cache key ignored the
		# caller's visible orgs, whichever request ran first would leak its
		# stats to the other.
		priv_org, priv_team = _make_org_team(
			"Private Stats Org", "private-stats-org", public=False
		)
		_make_trial(
			"Private T", "https://trial.example.com/priv", "Recruiting", [priv_team]
		)
		scheme = _make_api_scheme(priv_org, "stats-key")

		anon = APIClient()
		anon_resp = anon.get("/trials/stats/")
		self.assertEqual(anon_resp.status_code, 200)
		# Anonymous sees only the two public orgs' 4 trials.
		self.assertEqual(anon_resp.data["total"], 4)

		keyed = APIClient()
		keyed.credentials(HTTP_AUTHORIZATION=scheme.api_key)
		keyed_resp = keyed.get("/trials/stats/")
		self.assertEqual(keyed_resp.status_code, 200)
		# The org-scoped caller sees only its own org's single trial — if it
		# got the anonymous caller's cached payload this would be 4.
		self.assertEqual(keyed_resp.data["total"], 1)
		self.assertEqual(keyed_resp.data["recruiting"], 1)

		# And the reverse: a fresh anonymous request after the keyed one must
		# not pick up the keyed caller's entry.
		anon_again = anon.get("/trials/stats/")
		self.assertEqual(anon_again.data["total"], 4)


class TrialStatsRoutingTest(TrialStatsBase):
	"""/trials/stats/ resolves to the action; numeric detail lookups still work."""

	def test_stats_route_resolves_to_action(self):
		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		# The action returns the stats payload, not a trial detail or list.
		self.assertIn("total", resp.data)
		self.assertIn("recruiting", resp.data)
		self.assertIn("by_subject", resp.data)
		self.assertNotIn("results", resp.data)

	def test_numeric_detail_route_still_works(self):
		resp = self.client.get(f"/trials/{self.t1.trial_id}/")
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["trial_id"], self.t1.trial_id)
