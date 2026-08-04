"""
Tests for StatsView visibility enforcement and filtering.

Covers:
  - Anonymous caller: counts reflect public orgs only
  - Authenticated user (member of org): counts reflect own org
  - API key caller: counts reflect key's org
  - ?team=<id> for a hidden team → 404
  - ?team=<id> for a visible team → counts scoped to that team
  - ?include_public=true adds public org data for identified callers
  - ?organization= / ?org= alias: scoping, visibility, intersection with ?team=
  - Cache: second identical request served from cache (zero count queries)
  - assertNumQueries: pins the query budget for a typical scoped call

Run with:
    docker exec gregory python manage.py test api.tests.test_visibility_stats
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.timezone import now
from organizations.models import Organization, OrganizationUser
from rest_framework.test import APIClient

from api.models import APIAccessScheme
from gregory.models import Articles, Authors, OrganizationApiSettings, Sources, Subject, Team
from subscriptions.models import Lists, Subscribers

User = get_user_model()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_org(name, slug, public=False):
	org = Organization.objects.create(name=name, slug=slug)
	OrganizationApiSettings.objects.filter(organization=org).update(
		make_api_public=public
	)
	return org


def _make_team(org, name):
	slug = name.lower().replace(" ", "-")
	return Team.objects.create(organization=org, name=name, slug=slug)


def _make_subject(team, name):
	from django.utils.text import slugify

	return Subject.objects.create(
		team=team, subject_name=name, subject_slug=slugify(name)
	)


def _make_article(title, link, teams=(), subjects=()):
	art = Articles.objects.create(title=title, link=link)
	for t in teams:
		art.teams.add(t)
	for s in subjects:
		art.subjects.add(s)
	return art


def _make_trial(title, link, teams=(), subjects=()):
	from gregory.models import Trials

	trial = Trials.objects.create(title=title, link=link)
	for t in teams:
		trial.teams.add(t)
	for s in subjects:
		trial.subjects.add(s)
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


# ---------------------------------------------------------------------------
# Base setUp
# ---------------------------------------------------------------------------


class StatsVisibilityBase(TestCase):
	def setUp(self):
		self.my_org = _make_org("My Org", "my-org-stats", public=False)
		self.pub_org = _make_org("Public Org", "pub-org-stats", public=True)
		self.priv_org = _make_org("Private Org", "priv-org-stats", public=False)

		self.my_team = _make_team(self.my_org, "My Team Stats")
		self.pub_team = _make_team(self.pub_org, "Pub Team Stats")
		self.priv_team = _make_team(self.priv_org, "Priv Team Stats")

		# Articles
		self.art_mine = _make_article(
			"Mine Art", "https://st.ex/a1", teams=[self.my_team]
		)
		self.art_pub = _make_article(
			"Pub Art", "https://st.ex/a2", teams=[self.pub_team]
		)
		self.art_priv = _make_article(
			"Priv Art", "https://st.ex/a3", teams=[self.priv_team]
		)

		# Trials
		self.trial_mine = _make_trial(
			"Mine Trial", "https://st.ex/t1", teams=[self.my_team]
		)
		self.trial_pub = _make_trial(
			"Pub Trial", "https://st.ex/t2", teams=[self.pub_team]
		)
		self.trial_priv = _make_trial(
			"Priv Trial", "https://st.ex/t3", teams=[self.priv_team]
		)

		self.client = APIClient()


# ---------------------------------------------------------------------------
# Anonymous caller
# ---------------------------------------------------------------------------


class AnonymousStatsVisibilityTest(StatsVisibilityBase):
	"""Anonymous request → only public org data counted."""

	def test_articles_count_only_public(self):
		resp = self.client.get("/stats/")
		self.assertEqual(resp.status_code, 200)
		# Exactly 1 public article (pub_team) visible; mine and priv are hidden
		self.assertEqual(resp.data["articles"], 1)
		self.assertEqual(resp.data["trials"], 1)
		resp_pub = self.client.get("/stats/", {"team": self.pub_team.id})
		resp_priv = self.client.get("/stats/", {"team": self.priv_team.id})
		self.assertEqual(resp_pub.status_code, 200)
		# Hidden team → 404
		self.assertEqual(resp_priv.status_code, 404)

	def test_hidden_team_param_returns_404(self):
		resp = self.client.get("/stats/", {"team": self.priv_team.id})
		self.assertEqual(resp.status_code, 404)

	def test_own_team_param_returns_404_for_anon(self):
		"""my_team belongs to a private org → anonymous can't see it."""
		resp = self.client.get("/stats/", {"team": self.my_team.id})
		self.assertEqual(resp.status_code, 404)

	def test_public_team_param_returns_200(self):
		resp = self.client.get("/stats/", {"team": self.pub_team.id})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["articles"], 1)
		self.assertEqual(resp.data["trials"], 1)

	def test_invalid_team_param_returns_400(self):
		resp = self.client.get("/stats/", {"team": "abc"})
		self.assertEqual(resp.status_code, 400)


# ---------------------------------------------------------------------------
# Authenticated user (member of my_org)
# ---------------------------------------------------------------------------


class AuthenticatedUserStatsVisibilityTest(StatsVisibilityBase):
	def setUp(self):
		super().setUp()
		self.user = User.objects.create_user(username="stats-member", password="pw")
		OrganizationUser.objects.create(organization=self.my_org, user=self.user)
		self.client.force_login(self.user)

	def test_articles_count_only_own_org(self):
		"""Authenticated user without include_public sees only own org."""
		resp = self.client.get("/stats/")
		self.assertEqual(resp.status_code, 200)
		# Scope to own team for a precise count
		resp_mine = self.client.get("/stats/", {"team": self.my_team.id})
		self.assertEqual(resp_mine.status_code, 200)
		self.assertEqual(resp_mine.data["articles"], 1)
		self.assertEqual(resp_mine.data["trials"], 1)

	def test_hidden_team_param_returns_404(self):
		resp = self.client.get("/stats/", {"team": self.priv_team.id})
		self.assertEqual(resp.status_code, 404)

	def test_public_team_hidden_without_flag(self):
		"""pub_team is not visible without ?include_public=true."""
		resp = self.client.get("/stats/", {"team": self.pub_team.id})
		self.assertEqual(resp.status_code, 404)

	def test_public_team_visible_with_include_public(self):
		resp = self.client.get(
			"/stats/", {"team": self.pub_team.id, "include_public": "true"}
		)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["articles"], 1)

	def test_include_public_expands_global_count(self):
		resp_no_flag = self.client.get("/stats/")
		resp_with_flag = self.client.get("/stats/?include_public=true")
		# Without flag: only own org → 1 article (mine)
		self.assertEqual(resp_no_flag.data["articles"], 1)
		# With flag: own org + public org → 2 articles (mine + pub)
		self.assertEqual(resp_with_flag.data["articles"], 2)
		# Private org article never counted
		self.assertLess(resp_with_flag.data["articles"], 3)


# ---------------------------------------------------------------------------
# API key caller (bound to my_org)
# ---------------------------------------------------------------------------


class APIKeyStatsVisibilityTest(StatsVisibilityBase):
	def setUp(self):
		super().setUp()
		self.scheme = _make_api_scheme(self.my_org, "stats-key")
		self.client.credentials(HTTP_AUTHORIZATION=self.scheme.api_key)

	def test_own_team_visible(self):
		resp = self.client.get("/stats/", {"team": self.my_team.id})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["articles"], 1)
		self.assertEqual(resp.data["trials"], 1)

	def test_hidden_team_returns_404(self):
		resp = self.client.get("/stats/", {"team": self.priv_team.id})
		self.assertEqual(resp.status_code, 404)

	def test_public_team_hidden_without_flag(self):
		resp = self.client.get("/stats/", {"team": self.pub_team.id})
		self.assertEqual(resp.status_code, 404)

	def test_public_team_visible_with_include_public(self):
		resp = self.client.get(
			"/stats/", {"team": self.pub_team.id, "include_public": "true"}
		)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["articles"], 1)

	def test_include_public_expands_global_count(self):
		resp_no_flag = self.client.get("/stats/")
		resp_with_flag = self.client.get("/stats/?include_public=true")
		# Without flag: only own org → 1 article (mine)
		self.assertEqual(resp_no_flag.data["articles"], 1)
		# With flag: own org + public org → 2 articles (mine + pub)
		self.assertEqual(resp_with_flag.data["articles"], 2)
		# Private org article never counted
		self.assertLess(resp_with_flag.data["articles"], 3)


# ---------------------------------------------------------------------------
# Null-org API key (anonymous-equivalent)
# ---------------------------------------------------------------------------
class OrgFilterStatsTest(StatsVisibilityBase):
	"""?organization= scopes counts and enforces visibility."""

	def setUp(self):
		super().setUp()
		from django.core.cache import cache

		cache.clear()

	def test_org_filter_visible_org_scopes_counts(self):
		"""?organization=<public org> returns only that org's counts."""
		resp = self.client.get("/stats/", {"organization": self.pub_org.id})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["articles"], 1)
		self.assertEqual(resp.data["trials"], 1)

	def test_org_filter_hidden_org_returns_404(self):
		"""?organization=<private org> (not visible to anon) returns 404."""
		resp = self.client.get("/stats/", {"organization": self.priv_org.id})
		self.assertEqual(resp.status_code, 404)

	def test_org_filter_mixed_visible_hidden_returns_404(self):
		"""Any hidden org in a comma-separated list returns 404."""
		resp = self.client.get(
			"/stats/",
			{"organization": f"{self.pub_org.id},{self.priv_org.id}"},
		)
		self.assertEqual(resp.status_code, 404)

	def test_org_filter_invalid_value_returns_400(self):
		resp = self.client.get("/stats/", {"organization": "abc"})
		self.assertEqual(resp.status_code, 400)

	def test_org_alias_param_accepted(self):
		"""?org= is accepted as an alias for ?organization=."""
		resp = self.client.get("/stats/", {"org": self.pub_org.id})
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["articles"], 1)


class OrgAndTeamIntersectionTest(StatsVisibilityBase):
	"""?organization= + ?team= intersection semantics.

	Uses an authenticated member of my_org with ?include_public=true so that
	both my_org and pub_org are visible, allowing precise intersection tests.
	"""

	def setUp(self):
		super().setUp()
		from django.core.cache import cache

		cache.clear()
		self.user = User.objects.create_user(username="intersect-member", password="pw")
		OrganizationUser.objects.create(organization=self.my_org, user=self.user)
		self.client.force_login(self.user)

	def test_team_in_org_returns_correct_count(self):
		"""team belonging to the requested org → counts scoped to that team."""
		resp = self.client.get(
			"/stats/",
			{
				"organization": self.pub_org.id,
				"team": self.pub_team.id,
				"include_public": "true",
			},
		)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["articles"], 1)

	def test_team_not_in_org_returns_zero_not_404(self):
		"""Both team and org are visible but team belongs to a different org.

		my_team (in my_org) + ?organization=pub_org → intersection empty
		→ 200 with zero counts (both params individually valid, result is empty).
		"""
		resp = self.client.get(
			"/stats/",
			{
				"organization": self.pub_org.id,
				"team": self.my_team.id,
				"include_public": "true",
			},
		)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["articles"], 0)

	def test_hidden_team_with_visible_org_returns_404(self):
		"""A hidden team requested alongside a visible org is still 404."""
		resp = self.client.get(
			"/stats/",
			{
				"organization": self.pub_org.id,
				"team": self.priv_team.id,
				"include_public": "true",
			},
		)
		self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


class StatsCacheTest(StatsVisibilityBase):
	"""The second identical request within the TTL is served from cache."""

	def setUp(self):
		super().setUp()
		from django.core.cache import cache

		cache.clear()
		# Second public org/team so both cache entries can be populated by an
		# anonymous caller (my_team is private; only public teams are visible).
		self.pub_org2 = _make_org("Public Org 2", "pub-org2-cache-stats", public=True)
		self.pub_team2 = _make_team(self.pub_org2, "Pub Team 2 Cache Stats")
		_make_article(
			"Pub Art 2 Cache", "https://st.ex/cache/a2", teams=[self.pub_team2]
		)

	def test_second_request_served_from_cache(self):
		"""Two identical requests issue DB count queries only on the first."""
		from django.db import connection, reset_queries
		from django.conf import settings as django_settings

		orig_debug = django_settings.DEBUG
		django_settings.DEBUG = True
		try:
			reset_queries()
			self.client.get("/stats/", {"team": self.pub_team.id})
			first_count = len(connection.queries)

			reset_queries()
			self.client.get("/stats/", {"team": self.pub_team.id})
			second_count = len(connection.queries)
		finally:
			django_settings.DEBUG = orig_debug

		# The second call must issue fewer queries than the first
		# (cache hit replaces the four COUNT queries with one SELECT).
		self.assertLess(second_count, first_count)

	def test_cache_key_differs_by_team(self):
		"""Requests for different visible teams are cached independently."""
		from django.core.cache import cache as django_cache

		self.client.get("/stats/", {"team": self.pub_team.id})
		self.client.get("/stats/", {"team": self.pub_team2.id})

		# Both requests must produce distinct, non-None cache entries.
		key1 = f"stats:{self.pub_team.id}:subj:all"
		key2 = f"stats:{self.pub_team2.id}:subj:all"
		self.assertIsNotNone(django_cache.get(key1))
		self.assertIsNotNone(django_cache.get(key2))
		self.assertNotEqual(key1, key2)

	def test_cache_cleared_between_tests(self):
		"""setUp.cache.clear() isolates test runs."""
		from django.core.cache import cache as django_cache

		self.assertIsNone(django_cache.get("stats:all:subj:all"))


# ---------------------------------------------------------------------------
# Query-count regression guard
# ---------------------------------------------------------------------------


class StatsQueryCountTest(StatsVisibilityBase):
	"""assertNumQueries pins the query budget so regressions are caught."""

	def setUp(self):
		super().setUp()
		from django.core.cache import cache

		cache.clear()

	def test_scoped_call_query_budget(self):
		"""
		A team-scoped /stats/ call must stay within a small query budget.

		Budget breakdown (cold cache, 8 queries with the test suite's
		LocMemCache — CACHES in admin.settings_test — which never touches
		the DB; production's DatabaseCache adds a cache GET/SET pair plus a
		cull COUNT and SAVEPOINT/RELEASE, hence the generous <=18 ceiling):
		  1 — VisibleOrgMiddleware: OrganizationApiSettings lookup
		  1 — resolve team_id_list (Team VALUES) — doubles as the
		      team-visibility check when ?organization= is absent, since
		      that predicate is identical to a standalone visibility query
		      (see StatsView.get); no separate COUNT is issued
		  1 — resolve visible_subjects (Subject VALUES) — always run, to
		      build the by_subject roster; here it comes back empty (this
		      base fixture has no subjects) so the 3 by_subject group-by
		      queries below are skipped
		  4 — articles, trials, authors, subscribers COUNT DISTINCT
		  1 — sources VALUES
		= 8 queries. A call whose scope actually contains subjects adds up
		to 3 more (articles/trials/authors group-bys for by_subject) — see
		docs/spec-stats-subject-filter.md §4.6.
		"""
		from django.db import connection
		from django.test.utils import CaptureQueriesContext

		with CaptureQueriesContext(connection) as ctx:
			self.client.get("/stats/", {"team": self.pub_team.id})
		self.assertLessEqual(
			len(ctx.captured_queries),
			18,
			msg=f"StatsView exceeded the query budget: {len(ctx.captured_queries)} queries",
		)

	def test_cached_call_query_budget(self):
		"""A cache-warm call must issue far fewer queries than a cold one."""
		from django.db import connection
		from django.test.utils import CaptureQueriesContext

		self.client.get("/stats/", {"team": self.pub_team.id})  # warm the cache
		with CaptureQueriesContext(connection) as ctx:
			self.client.get("/stats/", {"team": self.pub_team.id})
		# <=4, not a pinned exact count: OrganizationApiSettings lookup (1)
		# + team_id_list resolution, which also serves as the
		# team-visibility check (1) + visible_subjects resolution, which
		# also serves as the ?subject= 404 check and runs before the cache
		# lookup so a cache hit can't bypass it (1) + cache GET (0 or 1
		# depending on backend). LocMemCache (admin.settings_test, what
		# CI's pytest run uses) answers GET in-process with no SQL, landing
		# at 3; DatabaseCache backends (production, and admin.settings's
		# default used when this test is invoked via `manage.py test`
		# instead of pytest) add one SQL round-trip for the GET, landing at
		# 4. Either way this must stay far below the cold-cache budget above.
		self.assertLessEqual(
			len(ctx.captured_queries),
			4,
			msg=f"Cache hit should eliminate the count queries: {len(ctx.captured_queries)} queries",
		)


# ---------------------------------------------------------------------------
# ?subject= filtering and by_subject facet
# ---------------------------------------------------------------------------


class SubjectStatsBase(StatsVisibilityBase):
	"""Shared fixture: subjects, tagged articles/trials, a source, a list."""

	def setUp(self):
		super().setUp()
		from django.core.cache import cache

		cache.clear()

		self.user = User.objects.create_user(username="subj-member", password="pw")
		OrganizationUser.objects.create(organization=self.my_org, user=self.user)
		self.client.force_login(self.user)
		self.include_public = {"include_public": "true"}

		self.subj_a = _make_subject(self.my_team, "Subject A")
		self.subj_b = _make_subject(self.my_team, "Subject B")
		self.subj_pub = _make_subject(self.pub_team, "Subject Pub")
		self.subj_priv = _make_subject(self.priv_team, "Subject Priv")

		# art_mine / trial_mine (from the base fixture) get tagged with subj_a.
		self.art_mine.subjects.add(self.subj_a)
		self.trial_mine.subjects.add(self.subj_a)

		self.art_mine2 = _make_article(
			"Mine Art 2", "https://st.ex/subj/a4", teams=[self.my_team], subjects=[self.subj_b]
		)

		# Article tagged with subj_a but belonging to no team — the §4.4 leak
		# guard: a ?team=my_team&subject=subj_a call must exclude it.
		self.art_teamless = _make_article(
			"Teamless", "https://st.ex/subj/teamless", teams=[], subjects=[self.subj_a]
		)

		self.source_a = Sources.objects.create(
			name="Src A",
			link="https://src-a.example.com/feed",
			team=self.my_team,
			subject=self.subj_a,
			source_for="science paper",
		)
		self.source_null = Sources.objects.create(
			name="Src Null",
			link="https://src-null.example.com/feed",
			team=self.my_team,
			subject=None,
			source_for="science paper",
		)

		self.list_a = Lists.objects.create(list_name="List A", team=self.my_team)
		self.list_a.subjects.add(self.subj_a)
		self.sub_a = Subscribers.objects.create(
			first_name="Sub", last_name="A", email="suba-stats@example.com", active=True
		)
		self.sub_a.subscriptions.add(self.list_a)

		self.list_none = Lists.objects.create(list_name="List None", team=self.my_team)
		self.sub_none = Subscribers.objects.create(
			first_name="Sub", last_name="None", email="subnone-stats@example.com", active=True
		)
		self.sub_none.subscriptions.add(self.list_none)


class SubjectFilterStatsTest(SubjectStatsBase):
	"""?subject= scopes every count, unions across IDs, and validates input."""

	def test_subject_filter_scopes_counts(self):
		resp = self.client.get(
			"/stats/", {"subject": self.subj_a.id, **self.include_public}
		)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["articles"], 1)  # art_mine only
		self.assertEqual(resp.data["trials"], 1)  # trial_mine only
		self.assertEqual(resp.data["subscribers"], 1)  # sub_a only
		self.assertEqual(resp.data["sources"]["total"], 1)  # source_a only

	def test_subject_filter_unions_rather_than_intersects(self):
		resp = self.client.get(
			"/stats/",
			{"subject": f"{self.subj_a.id},{self.subj_b.id}", **self.include_public},
		)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["articles"], 2)  # art_mine + art_mine2

	def test_invalid_subject_param_returns_400(self):
		resp = self.client.get("/stats/", {"subject": "abc"})
		self.assertEqual(resp.status_code, 400)
		self.assertIn("Invalid subject parameter", resp.data["error"])

	def test_teamless_article_excluded_by_team_scope(self):
		"""§4.4 leak guard: team join stays in effect even with ?subject=."""
		resp = self.client.get(
			"/stats/", {"team": self.my_team.id, "subject": self.subj_a.id}
		)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["articles"], 1)  # art_mine, not art_teamless

	def test_source_with_null_subject_dropped_when_filtering(self):
		resp_unfiltered = self.client.get("/stats/", {"team": self.my_team.id})
		self.assertEqual(resp_unfiltered.data["sources"]["total"], 2)

		resp_filtered = self.client.get(
			"/stats/", {"team": self.my_team.id, "subject": self.subj_a.id}
		)
		self.assertEqual(resp_filtered.data["sources"]["total"], 1)

	def test_list_without_subjects_contributes_no_subscribers(self):
		resp = self.client.get(
			"/stats/", {"team": self.my_team.id, "subject": self.subj_a.id}
		)
		self.assertEqual(resp.data["subscribers"], 1)  # sub_a, not sub_none


class SubjectVisibilityStatsTest(SubjectStatsBase):
	"""A subject in a non-visible org is 404, same as team/organization."""

	def test_hidden_org_subject_404_for_anonymous(self):
		anon = APIClient()
		resp = anon.get("/stats/", {"subject": self.subj_priv.id})
		self.assertEqual(resp.status_code, 404)

	def test_hidden_org_subject_404_for_api_key(self):
		scheme = _make_api_scheme(self.my_org, "subj-visibility-key")
		key_client = APIClient()
		key_client.credentials(HTTP_AUTHORIZATION=scheme.api_key)
		resp = key_client.get("/stats/", {"subject": self.subj_priv.id})
		self.assertEqual(resp.status_code, 404)

	def test_public_org_subject_404_without_include_public(self):
		resp = self.client.get("/stats/", {"subject": self.subj_pub.id})
		self.assertEqual(resp.status_code, 404)

	def test_public_org_subject_200_with_include_public(self):
		resp = self.client.get(
			"/stats/", {"subject": self.subj_pub.id, **self.include_public}
		)
		self.assertEqual(resp.status_code, 200)

	def test_mixed_visible_hidden_subject_returns_404(self):
		resp = self.client.get(
			"/stats/",
			{"subject": f"{self.subj_a.id},{self.subj_priv.id}", **self.include_public},
		)
		self.assertEqual(resp.status_code, 404)


class SubjectTeamIntersectionStatsTest(SubjectStatsBase):
	"""?team= + ?subject= intersection: zero payload, not 404, on mismatch."""

	def test_team_and_subject_of_other_team_returns_zero_not_404(self):
		resp = self.client.get(
			"/stats/",
			{"team": self.pub_team.id, "subject": self.subj_a.id, **self.include_public},
		)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["articles"], 0)
		self.assertEqual(resp.data["trials"], 0)
		self.assertEqual(resp.data["by_subject"], [])

	def test_team_and_its_own_subject_returns_counts(self):
		resp = self.client.get(
			"/stats/", {"team": self.my_team.id, "subject": self.subj_a.id}
		)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["articles"], 1)


class SubjectStatsCacheTest(SubjectStatsBase):
	"""Subject-filtered payloads must not share a cache entry with others."""

	def test_team_only_and_team_plus_subject_do_not_share_cache(self):
		from django.core.cache import cache as django_cache

		self.client.get("/stats/", {"team": self.my_team.id})
		self.client.get(
			"/stats/", {"team": self.my_team.id, "subject": self.subj_a.id}
		)
		key_unfiltered = f"stats:{self.my_team.id}:subj:all"
		key_filtered = f"stats:{self.my_team.id}:subj:{self.subj_a.id}"
		cached_unfiltered = django_cache.get(key_unfiltered)
		cached_filtered = django_cache.get(key_filtered)
		self.assertIsNotNone(cached_unfiltered)
		self.assertIsNotNone(cached_filtered)
		self.assertNotEqual(cached_unfiltered["articles"], cached_filtered["articles"])

	def test_different_subject_filters_cached_independently(self):
		from django.core.cache import cache as django_cache

		self.client.get(
			"/stats/", {"team": self.my_team.id, "subject": self.subj_a.id}
		)
		self.client.get(
			"/stats/", {"team": self.my_team.id, "subject": self.subj_b.id}
		)
		key_a = f"stats:{self.my_team.id}:subj:{self.subj_a.id}"
		key_b = f"stats:{self.my_team.id}:subj:{self.subj_b.id}"
		self.assertIsNotNone(django_cache.get(key_a))
		self.assertIsNotNone(django_cache.get(key_b))

	def test_reordered_subject_list_shares_cache_key(self):
		from django.core.cache import cache as django_cache

		self.client.get(
			"/stats/",
			{"team": self.my_team.id, "subject": f"{self.subj_a.id},{self.subj_b.id}"},
		)
		sorted_ids = ",".join(
			str(i) for i in sorted([self.subj_a.id, self.subj_b.id])
		)
		key_sorted = f"stats:{self.my_team.id}:subj:{sorted_ids}"
		self.assertIsNotNone(django_cache.get(key_sorted))

		resp2 = self.client.get(
			"/stats/",
			{"team": self.my_team.id, "subject": f"{self.subj_b.id},{self.subj_a.id}"},
		)
		self.assertEqual(django_cache.get(key_sorted), resp2.data)


class BySubjectFacetTest(StatsVisibilityBase):
	"""The by_subject breakdown: roster, ordering, and per-subject counts."""

	def setUp(self):
		super().setUp()
		from django.core.cache import cache

		cache.clear()

		self.user = User.objects.create_user(username="facet-member", password="pw")
		OrganizationUser.objects.create(organization=self.my_org, user=self.user)
		self.client.force_login(self.user)

		# Zero-count subject (name sorts after "Alpha"/"Beta").
		self.subj_zeta = _make_subject(self.my_team, "Zeta Subject")
		self.subj_alpha = _make_subject(self.my_team, "Alpha Subject")
		self.subj_beta = _make_subject(self.my_team, "Beta Subject")
		# Belongs to a private org invisible to this caller.
		self.subj_hidden = _make_subject(self.priv_team, "Hidden Subject")

		self.author1 = Authors.objects.create(given_name="Ann", family_name="One")
		self.author2 = Authors.objects.create(given_name="Bob", family_name="Two")

		# Tagged with both subj_alpha and the invisible subj_hidden: the
		# by_subject roster must still drop subj_hidden (leak guard).
		self.art_alpha1 = _make_article(
			"Alpha Art 1",
			"https://facet.ex/a1",
			teams=[self.my_team],
			subjects=[self.subj_alpha, self.subj_hidden],
		)
		self.art_alpha1.authors.add(self.author1)

		self.art_alpha2 = _make_article(
			"Alpha Art 2",
			"https://facet.ex/a2",
			teams=[self.my_team],
			subjects=[self.subj_alpha],
		)
		self.art_alpha2.authors.add(self.author2)

		# Two feeds, same domain, both under subj_alpha → 1 domain for alpha.
		Sources.objects.create(
			name="Alpha Feed 1",
			link="https://shared-domain.example.com/feed1",
			team=self.my_team,
			subject=self.subj_alpha,
			source_for="science paper",
		)
		Sources.objects.create(
			name="Alpha Feed 2",
			link="https://shared-domain.example.com/feed2",
			team=self.my_team,
			subject=self.subj_alpha,
			source_for="science paper",
		)
		# Same domain again, but under subj_beta → domain appears in both
		# rows while counting once in the top-level sources.total.
		Sources.objects.create(
			name="Beta Feed",
			link="https://shared-domain.example.com/feed3",
			team=self.my_team,
			subject=self.subj_beta,
			source_for="science paper",
		)
		# No subject at all → must not appear in any by_subject row.
		Sources.objects.create(
			name="Null Feed",
			link="https://null-domain.example.com/feed",
			team=self.my_team,
			subject=None,
			source_for="science paper",
		)

	def _by_subject(self, resp):
		return {row["subject_id"]: row for row in resp.data["by_subject"]}

	def test_roster_includes_zero_count_subjects(self):
		resp = self.client.get("/stats/", {"team": self.my_team.id})
		rows = self._by_subject(resp)
		self.assertIn(self.subj_zeta.id, rows)
		self.assertEqual(rows[self.subj_zeta.id]["articles"], 0)
		self.assertEqual(rows[self.subj_zeta.id]["trials"], 0)
		self.assertEqual(rows[self.subj_zeta.id]["sources"], 0)

	def test_roster_ordered_by_subject_name(self):
		resp = self.client.get("/stats/", {"team": self.my_team.id})
		names = [row["subject_name"] for row in resp.data["by_subject"]]
		self.assertEqual(names, sorted(names))

	def test_roster_respects_subject_filter(self):
		resp = self.client.get(
			"/stats/", {"team": self.my_team.id, "subject": self.subj_alpha.id}
		)
		ids = [row["subject_id"] for row in resp.data["by_subject"]]
		self.assertEqual(ids, [self.subj_alpha.id])

	def test_hidden_org_subject_excluded_despite_visible_article_tag(self):
		resp = self.client.get("/stats/", {"team": self.my_team.id})
		ids = {row["subject_id"] for row in resp.data["by_subject"]}
		self.assertNotIn(self.subj_hidden.id, ids)

	def test_per_subject_authors_counted_once_each(self):
		resp = self.client.get("/stats/", {"team": self.my_team.id})
		rows = self._by_subject(resp)
		self.assertEqual(rows[self.subj_alpha.id]["authors"], 2)
		self.assertEqual(resp.data["authors"], 2)

	def test_per_subject_sources_counts_distinct_domains(self):
		resp = self.client.get("/stats/", {"team": self.my_team.id})
		rows = self._by_subject(resp)
		# Two feeds, same domain, same subject → still 1.
		self.assertEqual(rows[self.subj_alpha.id]["sources"], 1)
		# Domain shared with alpha, different subject → appears here too.
		self.assertEqual(rows[self.subj_beta.id]["sources"], 1)
		# Shared domain counted once overall despite feeding two subjects,
		# plus the null-domain source → 2 total.
		self.assertEqual(resp.data["sources"]["total"], 2)

	def test_no_subscribers_key_in_by_subject_rows(self):
		resp = self.client.get("/stats/", {"team": self.my_team.id})
		for row in resp.data["by_subject"]:
			self.assertNotIn("subscribers", row)
