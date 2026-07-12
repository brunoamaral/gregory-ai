"""
Regression tests for Fix 4 of the write-endpoint hardening pass:

  - The quota-counting helpers (getNumberOfCallsInLast{Minute,Hour,Day})
    must scope their COUNT by api_access_scheme, so one client's call
    volume never counts against another client's quota.
  - ApiKeyMiddleware must resolve request.api_access_scheme via the
    lightweight gregory.visibility._resolve_api_scheme helper, which does
    NOT run any of the quota COUNT queries. Those queries are meant to run
    only inside checkValidAccess, as called directly by the write
    endpoints (post_article / edit_article / edit_trial) -- never for a
    plain GET.

Run with:
    docker exec gregory python manage.py test api.tests.test_api_quota_isolation
"""

from datetime import timedelta

from django.db import connection
from django.test import TestCase, Client
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now
from organizations.models import Organization

from api.models import APIAccessScheme, APIAccessSchemeLog
from api.utils.utils import (
	getNumberOfCallsInLastDay,
	getNumberOfCallsInLastHour,
	getNumberOfCallsInLastMinute,
)
from gregory.models import Articles, Team, OrganizationApiSettings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_org(name, slug=""):
	slug = slug or name.lower().replace(" ", "-")
	org = Organization.objects.create(name=name, slug=slug)
	OrganizationApiSettings.objects.filter(organization=org).update(
		make_api_public=False
	)
	return org


def _make_team(org, name):
	return Team.objects.create(
		organization=org, name=name, slug=name.lower().replace(" ", "-")
	)


def _make_scheme(org, name, ip_addresses=""):
	return APIAccessScheme.objects.create(
		client_name=name,
		client_contacts=f"{name}@example.com",
		organization=org,
		ip_addresses=ip_addresses,
		begin_date=now() - timedelta(days=1),
		end_date=now() + timedelta(days=30),
	)


def _log_call(scheme, when=None):
	return APIAccessSchemeLog.objects.create(
		call_type="POST /articles/post/",
		ip_addr="127.0.0.1",
		api_access_scheme=scheme,
		http_code=201,
		access_date=when or now(),
	)


# ---------------------------------------------------------------------------
# Helper-level quota isolation
# ---------------------------------------------------------------------------


class QuotaIsolationHelperTest(TestCase):
	def setUp(self):
		self.org = _make_org("Quota Org", "quota-isolation-org")
		self.scheme_a = _make_scheme(self.org, "quota-isolation-key-a")
		self.scheme_b = _make_scheme(self.org, "quota-isolation-key-b")

	def test_calls_for_scheme_a_do_not_count_against_scheme_b(self):
		for _ in range(5):
			_log_call(self.scheme_a)

		self.assertEqual(getNumberOfCallsInLastMinute(self.scheme_a), 5)
		self.assertEqual(getNumberOfCallsInLastHour(self.scheme_a), 5)
		self.assertEqual(getNumberOfCallsInLastDay(self.scheme_a), 5)

		self.assertEqual(getNumberOfCallsInLastMinute(self.scheme_b), 0)
		self.assertEqual(getNumberOfCallsInLastHour(self.scheme_b), 0)
		self.assertEqual(getNumberOfCallsInLastDay(self.scheme_b), 0)

	def test_calls_for_both_schemes_are_counted_independently(self):
		for _ in range(3):
			_log_call(self.scheme_a)
		for _ in range(7):
			_log_call(self.scheme_b)

		self.assertEqual(getNumberOfCallsInLastDay(self.scheme_a), 3)
		self.assertEqual(getNumberOfCallsInLastDay(self.scheme_b), 7)


# ---------------------------------------------------------------------------
# Middleware: GETs with a valid key must not run quota COUNT queries
# ---------------------------------------------------------------------------


class MiddlewareQuotaQueryTest(TestCase):
	def setUp(self):
		self.client = Client()
		self.org = _make_org("MW Org", "quota-isolation-mw-org")
		self.team = _make_team(self.org, "MW Team")
		self.scheme = _make_scheme(self.org, "quota-isolation-mw-key")
		article = Articles.objects.create(
			title="MW Article",
			link="https://example.com/quota-mw-article",
			doi="10.1234/quota-mw-article",
		)
		article.teams.add(self.team)

	def test_get_with_valid_key_does_not_run_quota_count_queries(self):
		with CaptureQueriesContext(connection) as ctx:
			resp = self.client.get(
				"/articles/", HTTP_AUTHORIZATION=self.scheme.api_key
			)
		self.assertEqual(resp.status_code, 200)

		offending = [
			q
			for q in ctx.captured_queries
			if "api_apiaccessschemelog" in q["sql"].lower()
		]
		self.assertEqual(
			offending,
			[],
			f"GET request unexpectedly ran queries against APIAccessSchemeLog: {offending}",
		)

	def test_get_with_valid_key_does_not_create_log_row(self):
		"""GETs are never written to APIAccessSchemeLog -- confirms the
		lightweight resolution path is truly a no-op on the log table."""
		before = APIAccessSchemeLog.objects.count()
		resp = self.client.get(
			"/articles/", HTTP_AUTHORIZATION=self.scheme.api_key
		)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(APIAccessSchemeLog.objects.count(), before)
