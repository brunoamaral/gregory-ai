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

Buckets are counted on ``recruitment_status_normalized`` (the canonical vocabulary in
``gregory.utils.trial_field_normalizers``), not the raw ``recruitment_status`` column, so
this suite also locks in:

  - per-registry spelling variants of the same canonical status ("RECRUITING",
    "Recruiting", "Ongoing, recruiting") all land in the same bucket
  - WHO's generic "Not recruiting" lands in its own ``not_recruiting`` bucket, not
    ``active_not_recruiting``
  - CT.gov's "UNKNOWN" lands in ``unknown``; its expanded-access "AVAILABLE" lands in
    ``other``
  - a null ``recruitment_status`` lands in ``no_status``
  - every canonical ``TrialRecruitmentStatus`` key is present in the response even when
    its count is 0, and none of the old ad-hoc keys (available/not_available/
    withheld/authorised) survive
  - the ``recruitment_status_normalized`` and ``phase_normalized`` query params scope the
    totals end-to-end (the filtering itself comes free via ``filter_queryset``)

``/trials/stats/`` also returns facet breakdowns (``by_phase``, ``by_region``,
``by_country``, ``by_year``, ``by_study_type``) over the filtered queryset. This suite
locks in:

  - by_phase: every ``TrialPhase`` key always present (0 when empty); raw spelling
    variants of the same phase land in one bucket; a null raw phase lands in
    ``no_phase``; values (incl. ``no_phase``) sum to ``total``; scoped by filters
  - by_country: a multi-country trial appears once per country; sorted by
    ``-count`` then code with zero-count countries omitted; a trial with no
    ``TrialCountry`` rows surfaces as a trailing ``{"country": None, ...}`` entry;
    Count(distinct=True) guards the two-team double-count case here too; the
    ``?country=DE`` self-faceting interaction is pinned with an explanatory comment
  - by_region: all 6 ``TrialRegion`` keys always present; a multi-region trial is
    counted once per region; ``no_region`` catches null/empty ``regions_normalized``
  - by_year: ascending by year, zero-count years omitted, a null
    ``date_registration`` surfaces as a trailing ``{"year": None, ...}`` entry;
    values sum to ``total``
  - by_study_type: every ``TrialStudyType`` key always present (0 when empty); raw
    spelling variants of the same study type land in one bucket; a null raw study_type
    lands in ``no_study_type``; values (incl. ``no_study_type``) sum to ``total``;
    scoped by filters

Run with:
    docker exec gregory python manage.py test api.tests.test_trials_stats
"""

from datetime import date, timedelta

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now
from organizations.models import Organization
from rest_framework.test import APIClient

from api.models import APIAccessScheme
from gregory.models import OrganizationApiSettings, Subject, Team, Trials
from gregory.utils.trial_field_normalizers import (
	TrialPhase,
	TrialRecruitmentStatus,
	TrialRegion,
	TrialSexEligibility,
	TrialStudyType,
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


def _make_trial(
	title, link, status, teams, subjects=(), phase=None, countries=None,
	date_registration=None, study_type=None,
):
	trial = Trials.objects.create(
		title=title,
		link=link,
		recruitment_status=status,
		phase=phase,
		countries=countries,
		date_registration=date_registration,
		study_type=study_type,
	)
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


class TrialStatsNormalizedBucketsTest(TrialStatsBase):
	"""Buckets are counted on recruitment_status_normalized, the canonical vocabulary in
	gregory.utils.trial_field_normalizers — not a second hand-rolled spelling list."""

	def test_recruiting_spelling_variants_all_land_in_recruiting(self):
		# "RECRUITING" (CT.gov upper-case) and "Ongoing, recruiting" (EU CTIS) both
		# normalize to the same canonical bucket as the fixture's "Recruiting".
		_make_trial("R1", "https://trial.example.com/r1", "RECRUITING", [self.team])
		_make_trial(
			"R2", "https://trial.example.com/r2", "Ongoing, recruiting", [self.team]
		)
		resp = self.client.get("/trials/stats/", {"team_id": self.team.id})
		self.assertEqual(resp.status_code, 200)
		# t1, t2 ("Recruiting") + R1 + R2 = 4.
		self.assertEqual(resp.data["recruiting"], 4)

	def test_who_not_recruiting_lands_in_not_recruiting_not_active(self):
		_make_trial(
			"NR", "https://trial.example.com/nr", "Not recruiting", [self.team]
		)
		resp = self.client.get("/trials/stats/", {"team_id": self.team.id})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["not_recruiting"], 1)
		# WHO's generic status doesn't say the trial is active — it must not be folded
		# into active_not_recruiting.
		self.assertEqual(resp.data["active_not_recruiting"], 0)

	def test_ctgov_unknown_lands_in_unknown_bucket(self):
		_make_trial("UNK", "https://trial.example.com/unk", "UNKNOWN", [self.team])
		resp = self.client.get("/trials/stats/", {"team_id": self.team.id})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["unknown"], 1)

	def test_ctgov_available_lands_in_other_bucket(self):
		_make_trial("AV", "https://trial.example.com/av", "AVAILABLE", [self.team])
		resp = self.client.get("/trials/stats/", {"team_id": self.team.id})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["other"], 1)

	def test_null_recruitment_status_lands_in_no_status(self):
		_make_trial(
			"NoStatus", "https://trial.example.com/nostatus", None, [self.team]
		)
		resp = self.client.get("/trials/stats/", {"team_id": self.team.id})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["no_status"], 1)

	def test_every_canonical_key_present_even_when_zero(self):
		# other_team has only t4 (TERMINATED), so most buckets are 0 here — the point is
		# that the zero-count keys are still in the payload, not omitted.
		resp = self.client.get("/trials/stats/", {"team_id": self.other_team.id})
		self.assertEqual(resp.status_code, 200)
		for value in TrialRecruitmentStatus.values:
			self.assertIn(value, resp.data)
		self.assertEqual(resp.data["recruiting"], 0)
		self.assertEqual(resp.data["terminated"], 1)
		# The old hand-rolled keys must not survive the rewrite.
		for stale_key in ("available", "not_available", "withheld", "authorised"):
			self.assertNotIn(stale_key, resp.data)


class TrialStatsNormalizedFilterScopingTest(TrialStatsBase):
	"""/trials/stats/ scopes totals via the normalized-field filters too — those filters
	come free through filter_queryset, but must be exercised end-to-end."""

	def test_recruitment_status_normalized_filter_scopes_totals(self):
		_make_trial(
			"CTIS-R",
			"https://trial.example.com/ctis-r",
			"Ongoing, recruiting",
			[self.team],
		)
		resp = self.client.get(
			"/trials/stats/", {"recruitment_status_normalized": "recruiting"}
		)
		self.assertEqual(resp.status_code, 200)
		# t1, t2 ("Recruiting") + the CTIS trial above = 3; t3/t4 are excluded.
		self.assertEqual(resp.data["total"], 3)
		self.assertEqual(resp.data["recruiting"], 3)

	def test_phase_normalized_filter_scopes_totals(self):
		_make_trial(
			"P3",
			"https://trial.example.com/p3",
			"Recruiting",
			[self.team],
			phase="Phase 3",
		)
		resp = self.client.get("/trials/stats/", {"phase_normalized": "phase_3"})
		self.assertEqual(resp.status_code, 200)
		# None of the fixture trials (t1-t4) have a phase set, so only the trial created
		# above matches.
		self.assertEqual(resp.data["total"], 1)
		self.assertEqual(resp.data["recruiting"], 1)


class TrialStatsPhaseFacetTest(TrialStatsBase):
	"""by_phase: enum shape, spelling-variant grouping, the no_phase bucket, filter scoping."""

	def test_every_phase_key_present_and_no_stray_keys(self):
		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		by_phase = resp.data["by_phase"]
		self.assertEqual(set(by_phase.keys()), set(TrialPhase.values) | {"no_phase"})
		# None of the fixture trials (t1-t4) have a phase set.
		for value in TrialPhase.values:
			self.assertEqual(by_phase[value], 0)
		self.assertEqual(by_phase["no_phase"], 4)

	def test_raw_spelling_variants_land_in_same_bucket(self):
		_make_trial(
			"P3a", "https://trial.example.com/p3a", "Recruiting", [self.team],
			phase="Phase III",
		)
		_make_trial(
			"P3b", "https://trial.example.com/p3b", "Recruiting", [self.team],
			phase="PHASE3",
		)
		resp = self.client.get("/trials/stats/", {"team_id": self.team.id})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["by_phase"]["phase_3"], 2)

	def test_null_raw_phase_lands_in_no_phase(self):
		# other_team has only t4, which has no phase set.
		resp = self.client.get("/trials/stats/", {"team_id": self.other_team.id})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["by_phase"]["no_phase"], 1)

	def test_by_phase_values_sum_to_total(self):
		_make_trial(
			"P2", "https://trial.example.com/p2", "Recruiting", [self.team],
			phase="Phase 2",
		)
		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(sum(resp.data["by_phase"].values()), resp.data["total"])

	def test_filter_scopes_by_phase(self):
		_make_trial(
			"P3", "https://trial.example.com/p3f", "Recruiting", [self.team],
			phase="Phase 3",
		)
		resp = self.client.get(
			"/trials/stats/", {"recruitment_status_normalized": "recruiting"}
		)
		self.assertEqual(resp.status_code, 200)
		# t1, t2 ("Recruiting", no phase) + the phase-3 trial above = 3 recruiting
		# trials; only the trial with a phase set lands in phase_3.
		self.assertEqual(resp.data["by_phase"]["phase_3"], 1)
		self.assertEqual(resp.data["by_phase"]["no_phase"], 2)


class TrialStatsStudyTypeFacetTest(TrialStatsBase):
	"""by_study_type: enum shape, spelling-variant grouping, the no_study_type bucket,
	filter scoping."""

	def test_every_study_type_key_present_and_no_stray_keys(self):
		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		by_study_type = resp.data["by_study_type"]
		self.assertEqual(
			set(by_study_type.keys()), set(TrialStudyType.values) | {"no_study_type"}
		)
		# None of the fixture trials (t1-t4) have a study_type set.
		for value in TrialStudyType.values:
			self.assertEqual(by_study_type[value], 0)
		self.assertEqual(by_study_type["no_study_type"], 4)

	def test_raw_spelling_variants_land_in_same_bucket(self):
		_make_trial(
			"STa", "https://trial.example.com/st-a", "Recruiting", [self.team],
			study_type="INTERVENTIONAL",
		)
		_make_trial(
			"STb", "https://trial.example.com/st-b", "Recruiting", [self.team],
			study_type="Interventional clinical trial of medicinal product",
		)
		resp = self.client.get("/trials/stats/", {"team_id": self.team.id})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["by_study_type"]["interventional"], 2)

	def test_null_raw_study_type_lands_in_no_study_type(self):
		# other_team has only t4, which has no study_type set.
		resp = self.client.get("/trials/stats/", {"team_id": self.other_team.id})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["by_study_type"]["no_study_type"], 1)

	def test_by_study_type_values_sum_to_total(self):
		_make_trial(
			"STc", "https://trial.example.com/st-c", "Recruiting", [self.team],
			study_type="OBSERVATIONAL",
		)
		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(sum(resp.data["by_study_type"].values()), resp.data["total"])

	def test_filter_scopes_by_study_type(self):
		_make_trial(
			"STd", "https://trial.example.com/st-d", "Recruiting", [self.team],
			study_type="INTERVENTIONAL",
		)
		resp = self.client.get(
			"/trials/stats/", {"recruitment_status_normalized": "recruiting"}
		)
		self.assertEqual(resp.status_code, 200)
		# t1, t2 ("Recruiting", no study_type) + the interventional trial above = 3
		# recruiting trials; only the trial with a study_type set lands in
		# interventional.
		self.assertEqual(resp.data["by_study_type"]["interventional"], 1)
		self.assertEqual(resp.data["by_study_type"]["no_study_type"], 2)


class TrialStatsSexFacetTest(TrialStatsBase):
	"""by_sex: enum shape, the "Female, Male" -> all regression guard, the no_sex_data
	bucket, filter scoping."""

	def _make_trial_with_gender(self, title, link, teams, inclusion_gender):
		trial = Trials.objects.create(
			title=title,
			link=link,
			recruitment_status="Recruiting",
			inclusion_gender=inclusion_gender,
		)
		for team in teams:
			trial.teams.add(team)
		return trial

	def test_every_sex_key_present_and_no_stray_keys(self):
		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		by_sex = resp.data["by_sex"]
		self.assertEqual(
			set(by_sex.keys()), set(TrialSexEligibility.values) | {"no_sex_data"}
		)
		# None of the fixture trials (t1-t4) have inclusion_gender set.
		for value in TrialSexEligibility.values:
			self.assertEqual(by_sex[value], 0)
		self.assertEqual(by_sex["no_sex_data"], 4)

	def test_female_comma_male_lands_in_all_not_female(self):
		"""Regression guard: the substring-match bug this normalization fixes must not
		resurface in the stats facet either."""
		self._make_trial_with_gender(
			"SXa", "https://trial.example.com/sx-a", [self.team], "Female, Male"
		)
		resp = self.client.get("/trials/stats/", {"team_id": self.team.id})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["by_sex"]["all"], 1)
		self.assertEqual(resp.data["by_sex"]["female"], 0)

	def test_raw_spelling_variants_land_in_same_bucket(self):
		self._make_trial_with_gender(
			"SXb", "https://trial.example.com/sx-b", [self.team], "Female"
		)
		self._make_trial_with_gender(
			"SXc", "https://trial.example.com/sx-c", [self.team], "Females"
		)
		resp = self.client.get("/trials/stats/", {"team_id": self.team.id})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["by_sex"]["female"], 2)

	def test_null_raw_inclusion_gender_lands_in_no_sex_data(self):
		# other_team has only t4, which has no inclusion_gender set.
		resp = self.client.get("/trials/stats/", {"team_id": self.other_team.id})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["by_sex"]["no_sex_data"], 1)

	def test_by_sex_values_sum_to_total(self):
		self._make_trial_with_gender(
			"SXd", "https://trial.example.com/sx-d", [self.team], "Male"
		)
		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(sum(resp.data["by_sex"].values()), resp.data["total"])

	def test_filter_scopes_by_sex(self):
		self._make_trial_with_gender(
			"SXe", "https://trial.example.com/sx-e", [self.team], "Female"
		)
		resp = self.client.get(
			"/trials/stats/", {"recruitment_status_normalized": "recruiting"}
		)
		self.assertEqual(resp.status_code, 200)
		# t1, t2 ("Recruiting", no inclusion_gender) + the female trial above = 3
		# recruiting trials; only the trial with inclusion_gender set lands in female.
		self.assertEqual(resp.data["by_sex"]["female"], 1)
		self.assertEqual(resp.data["by_sex"]["no_sex_data"], 2)


class TrialStatsCountryFacetTest(TrialStatsBase):
	"""by_country: multi-country fan-out, sort/omit rules, the null bucket, dedup, and the
	accepted ?country= self-faceting interaction."""

	def test_multi_country_trial_appears_in_both_country_entries(self):
		_make_trial(
			"DEFR", "https://trial.example.com/defr", "Recruiting", [self.team],
			countries="Germany, France",
		)
		resp = self.client.get("/trials/stats/", {"team_id": self.team.id})
		self.assertEqual(resp.status_code, 200)
		by_country = {row["country"]: row["count"] for row in resp.data["by_country"]}
		self.assertEqual(by_country.get("DE"), 1)
		self.assertEqual(by_country.get("FR"), 1)

	def test_sorted_by_count_desc_then_code_zero_counts_omitted(self):
		_make_trial(
			"DE1", "https://trial.example.com/de1", "Recruiting", [self.team],
			countries="Germany",
		)
		_make_trial(
			"DE2", "https://trial.example.com/de2", "Recruiting", [self.team],
			countries="Germany",
		)
		_make_trial(
			"FR1", "https://trial.example.com/fr1", "Recruiting", [self.team],
			countries="France",
		)
		resp = self.client.get("/trials/stats/", {"team_id": self.team.id})
		self.assertEqual(resp.status_code, 200)
		non_null = [
			(row["country"], row["count"])
			for row in resp.data["by_country"]
			if row["country"] is not None
		]
		self.assertEqual(non_null[0], ("DE", 2))
		self.assertIn(("FR", 1), non_null)
		# No zero-count country entries — every code present has count > 0.
		self.assertTrue(all(count > 0 for _country, count in non_null))

	def test_trial_with_no_country_data_is_trailing_null_entry(self):
		# other_team has only t4, which has no country data at all.
		resp = self.client.get("/trials/stats/", {"team_id": self.other_team.id})
		self.assertEqual(resp.status_code, 200)
		by_country = resp.data["by_country"]
		self.assertEqual(by_country[-1], {"country": None, "count": 1})

	def test_trial_visible_under_two_teams_counted_once_per_country(self):
		_make_trial(
			"Shared", "https://trial.example.com/shared-country", "Recruiting",
			[self.team, self.other_team], countries="Germany",
		)
		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		by_country = {
			row["country"]: row["count"]
			for row in resp.data["by_country"]
			if row["country"] is not None
		}
		self.assertEqual(by_country.get("DE"), 1)

	def test_country_query_param_no_longer_self_facets_by_country(self):
		# filter_country switched from a join+.iexact+.distinct() to an EXISTS
		# subquery for performance (8.8ms vs 21.8ms on the row fetch). The EXISTS subquery is independent of _by_country_counts's
		# own `.values("trial_countries__country")` join, so there is no shared JOIN
		# alias left to restrict — a multi-country trial filtered in by one of its
		# countries now surfaces ALL of its countries in by_country, not just the
		# one that matched the filter. This intentionally supersedes the old
		# self-faceting interaction pinned by this test.
		_make_trial(
			"DEFR", "https://trial.example.com/defr-self-facet", "Recruiting",
			[self.team], countries="Germany, France",
		)
		resp = self.client.get("/trials/stats/", {"country": "DE"})
		self.assertEqual(resp.status_code, 200)
		countries = {row["country"] for row in resp.data["by_country"]}
		self.assertEqual(countries, {"DE", "FR"})


class TrialStatsRegionFacetTest(TrialStatsBase):
	"""by_region: enum shape, multi-region fan-out, and the no_region bucket."""

	def test_all_region_keys_always_present(self):
		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		by_region = resp.data["by_region"]
		self.assertEqual(set(by_region.keys()), set(TrialRegion.values) | {"no_region"})
		# None of t1-t4 have country data, so every region is 0 and all four
		# trials land in no_region.
		for value in TrialRegion.values:
			self.assertEqual(by_region[value], 0)
		self.assertEqual(by_region["no_region"], 4)

	def test_multi_region_trial_counted_once_per_region(self):
		_make_trial(
			"DEUS", "https://trial.example.com/deus", "Recruiting", [self.team],
			countries="Germany, United States",
		)
		resp = self.client.get("/trials/stats/", {"team_id": self.team.id})
		self.assertEqual(resp.status_code, 200)
		by_region = resp.data["by_region"]
		self.assertEqual(by_region["europe"], 1)
		self.assertEqual(by_region["north_america"], 1)

	def test_no_region_counts_null_or_empty_regions(self):
		# other_team has only t4, whose regions_normalized is null.
		resp = self.client.get("/trials/stats/", {"team_id": self.other_team.id})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["by_region"]["no_region"], 1)


class TrialStatsYearFacetTest(TrialStatsBase):
	"""by_year: ascending order, zero-count omission, the trailing null bucket, and the
	total-sum invariant."""

	def test_ascending_years_zero_omitted_null_last(self):
		_make_trial(
			"Y2019", "https://trial.example.com/y2019", "Recruiting", [self.team],
			date_registration=date(2019, 3, 1),
		)
		_make_trial(
			"Y2020a", "https://trial.example.com/y2020a", "Recruiting", [self.team],
			date_registration=date(2020, 6, 1),
		)
		_make_trial(
			"Y2020b", "https://trial.example.com/y2020b", "Recruiting", [self.team],
			date_registration=date(2020, 9, 1),
		)
		resp = self.client.get(
			"/trials/stats/",
			{"team_id": self.team.id, "recruitment_status_normalized": "recruiting"},
		)
		self.assertEqual(resp.status_code, 200)
		# t1, t2 (recruiting, no date_registration) contribute to the trailing null
		# entry alongside the three dated trials above.
		self.assertEqual(
			[(row["year"], row["count"]) for row in resp.data["by_year"]],
			[(2019, 1), (2020, 2), (None, 2)],
		)

	def test_by_year_sums_to_total(self):
		_make_trial(
			"Y2021", "https://trial.example.com/y2021", "Recruiting", [self.team],
			date_registration=date(2021, 1, 1),
		)
		resp = self.client.get("/trials/stats/")
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(
			sum(row["count"] for row in resp.data["by_year"]), resp.data["total"]
		)
