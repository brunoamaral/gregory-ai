"""
Tests for TrialViewSet's opt-in ``include_stats`` query parameter.

Before this change, ``TrialViewSet.list()`` unconditionally re-ran
``filter_queryset(self.get_queryset())`` and executed a GROUP BY
recruitment_status aggregation over the full filtered queryset on *every*
paginated list request, whether or not the caller wanted the stats block.
This suite locks in:

  - the aggregation only runs when ``include_stats`` is truthy
    (``true``/``1``/``yes``, case-insensitive)
  - the default response has no ``stats`` key at all, and critically never
    runs the aggregation query
  - the stats totals are correct
  - stats respect other filters (e.g. ``team_id``) when combined with
    ``include_stats``

Run with:
    docker exec gregory python manage.py test api.tests.test_trials_stats
"""

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from organizations.models import Organization
from rest_framework.test import APIClient

from gregory.models import OrganizationApiSettings, Team, Trials


def _make_public_org_team(name, slug):
	org = Organization.objects.create(name=name, slug=slug)
	OrganizationApiSettings.objects.filter(organization=org).update(
		make_api_public=True
	)
	team = Team.objects.create(organization=org, name=name, slug=slug)
	return org, team


def _make_trial(title, link, status, teams):
	trial = Trials.objects.create(title=title, link=link, recruitment_status=status)
	for team in teams:
		trial.teams.add(team)
	return trial


class TrialStatsOptInTest(TestCase):
	def setUp(self):
		self.org, self.team = _make_public_org_team("Stats Org", "stats-org")
		self.other_org, self.other_team = _make_public_org_team(
			"Other Stats Org", "other-stats-org"
		)

		self.t1 = _make_trial(
			"T1", "https://trial.example.com/1", "Recruiting", [self.team]
		)
		self.t2 = _make_trial(
			"T2", "https://trial.example.com/2", "Recruiting", [self.team]
		)
		self.t3 = _make_trial(
			"T3", "https://trial.example.com/3", "COMPLETED", [self.team]
		)
		self.t4 = _make_trial(
			"T4", "https://trial.example.com/4", "TERMINATED", [self.other_team]
		)

		self.client = APIClient()

	# -- default behaviour: no stats key, no aggregation query -----------

	def test_default_list_has_no_stats_key(self):
		resp = self.client.get("/trials/")
		self.assertEqual(resp.status_code, 200)
		self.assertNotIn("stats", resp.data)

	def test_include_stats_false_or_absent_has_no_stats_key(self):
		for params in (
			{},
			{"include_stats": "false"},
			{"include_stats": "no"},
			{"include_stats": "0"},
			{"include_stats": "garbage"},
		):
			resp = self.client.get("/trials/", params)
			self.assertEqual(resp.status_code, 200)
			self.assertNotIn(
				"stats", resp.data, msg=f"params={params!r} should not include stats"
			)

	def test_default_list_does_not_run_stats_aggregation_query(self):
		with CaptureQueriesContext(connection) as ctx:
			resp = self.client.get("/trials/")
		self.assertEqual(resp.status_code, 200)
		for q in ctx.captured_queries:
			sql = q["sql"]
			self.assertFalse(
				"GROUP BY" in sql and "recruitment_status" in sql,
				msg=(
					"Unexpected stats aggregation query ran without "
					f"include_stats: {sql}"
				),
			)

	# -- include_stats=true: totals + aggregation query runs --------------

	def test_include_stats_true_returns_correct_totals(self):
		resp = self.client.get("/trials/", {"include_stats": "true"})
		self.assertEqual(resp.status_code, 200)
		self.assertIn("stats", resp.data)
		stats = resp.data["stats"]
		self.assertEqual(stats["total"], 4)
		self.assertEqual(stats["recruiting"], 2)
		self.assertEqual(stats["completed"], 1)
		self.assertEqual(stats["terminated"], 1)

	def test_include_stats_accepts_1_and_yes_case_insensitively(self):
		for value in ("1", "YES", "True", "TRUE", "yes"):
			resp = self.client.get("/trials/", {"include_stats": value})
			self.assertEqual(resp.status_code, 200)
			self.assertIn(
				"stats", resp.data, msg=f"include_stats={value!r} should enable stats"
			)

	def test_include_stats_true_runs_aggregation_query(self):
		with CaptureQueriesContext(connection) as ctx:
			resp = self.client.get("/trials/", {"include_stats": "true"})
		self.assertEqual(resp.status_code, 200)
		self.assertTrue(
			any(
				"GROUP BY" in q["sql"] and "recruitment_status" in q["sql"]
				for q in ctx.captured_queries
			),
			msg="Expected a GROUP BY recruitment_status aggregation query",
		)

	# -- include_stats combined with a filter (team_id) --------------------

	def test_include_stats_with_team_filter_scopes_totals(self):
		resp = self.client.get(
			"/trials/", {"include_stats": "true", "team_id": self.team.id}
		)
		self.assertEqual(resp.status_code, 200)
		stats = resp.data["stats"]
		self.assertEqual(stats["total"], 3)
		self.assertEqual(stats["recruiting"], 2)
		self.assertEqual(stats["completed"], 1)
		self.assertEqual(stats["terminated"], 0)

	def test_include_stats_with_other_team_filter_scopes_totals(self):
		resp = self.client.get(
			"/trials/", {"include_stats": "true", "team_id": self.other_team.id}
		)
		self.assertEqual(resp.status_code, 200)
		stats = resp.data["stats"]
		self.assertEqual(stats["total"], 1)
		self.assertEqual(stats["terminated"], 1)
		self.assertEqual(stats["recruiting"], 0)

	# -- distinct=True guards against double-counting a multi-team trial --

	def test_trial_visible_under_two_teams_is_not_double_counted(self):
		shared = _make_trial(
			"Shared",
			"https://trial.example.com/shared",
			"Recruiting",
			[self.team, self.other_team],
		)
		resp = self.client.get("/trials/", {"include_stats": "true"})
		self.assertEqual(resp.status_code, 200)
		stats = resp.data["stats"]
		# 4 original trials + 1 shared trial = 5, not 6 (would be 6 if the
		# shared trial were counted once per team via a non-distinct Count).
		self.assertEqual(stats["total"], 5)
		self.assertEqual(stats["recruiting"], 3)
		shared.delete()
