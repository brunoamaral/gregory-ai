"""
discovery_date_after / discovery_date_before on /articles/, /trials/ and
/patents/ (api.filters.DateOrDateTimeFilter).

The point is a fixed window ("discovered Tuesday to Tuesday") that a page can
cache and reuse for past weeks, which last_days (rolling from now) and
week/year (Monday to Sunday, articles only) can't give.

Fixture, all on one public subject, discovery dates in UTC:

  mon_late   2026-11-16 23:30   the evening before the window
  tue_early  2026-11-17 00:00   first moment of the window
  tue_send   2026-11-17 08:05   when the email goes out
  mon_end    2026-11-23 23:59   last minute of the window (by date)
  tue_next   2026-11-24 08:00   the next Tuesday morning
"""

from datetime import datetime, timezone as dt_timezone

from django.core.cache import cache
from django.test import TestCase
from django.utils.text import slugify
from rest_framework.test import APIClient

from api.tests.visibility_helpers import publish_subjects
from gregory.models import Articles, Patents, Subject, Team, Trials
from mcpauth.tests.helpers import make_org

MOMENTS = {
	"mon_late": datetime(2026, 11, 16, 23, 30, tzinfo=dt_timezone.utc),
	"tue_early": datetime(2026, 11, 17, 0, 0, tzinfo=dt_timezone.utc),
	"tue_send": datetime(2026, 11, 17, 8, 5, tzinfo=dt_timezone.utc),
	"mon_end": datetime(2026, 11, 23, 23, 59, tzinfo=dt_timezone.utc),
	"tue_next": datetime(2026, 11, 24, 8, 0, tzinfo=dt_timezone.utc),
}


class DiscoveryDateFilterTests(TestCase):
	@classmethod
	def setUpTestData(cls):
		org = make_org("Discovery Org")
		team = Team.objects.create(organization=org, name="Discovery team", slug="discovery-team")
		cls.subject = Subject.objects.create(
			team=team, subject_name="Discovery Subject", subject_slug="discovery-subject"
		)
		cls.site = publish_subjects(
			cls.subject, organization=org, domain="discovery.example.com", patents_enabled=True
		)
		for key, moment in MOMENTS.items():
			# discovery_date is auto_now_add on articles and patents, so set it
			# with update() after the row exists.
			article = Articles.objects.create(
				title=f"article {key}", link=f"https://a.test/{key}"
			)
			article.subjects.add(cls.subject)
			Articles.objects.filter(pk=article.pk).update(discovery_date=moment)

			trial = Trials.objects.create(
				title=f"trial {key}", link=f"https://t.test/{slugify(key)}"
			)
			trial.subjects.add(cls.subject)
			Trials.objects.filter(pk=trial.pk).update(discovery_date=moment)

			patent = Patents.objects.create(title=f"patent {key}")
			patent.subjects.add(cls.subject)
			Patents.objects.filter(pk=patent.pk).update(discovery_date=moment)

		# A trial with no discovery date at all: any bound excludes it.
		undated = Trials.objects.create(title="trial undated", link="https://t.test/undated")
		undated.subjects.add(cls.subject)
		Trials.objects.filter(pk=undated.pk).update(discovery_date=None)

	def setUp(self):
		cache.clear()
		self.client = APIClient()

	def keys(self, path, noun, **params):
		response = self.client.get(path, {"site_id": self.site.pk, **params})
		self.assertEqual(response.status_code, 200, response.content)
		prefix = f"{noun} "
		return sorted(r["title"][len(prefix):] for r in response.data["results"])

	def assert_all_endpoints(self, expected, **params):
		for path, noun in (("/articles/", "article"), ("/trials/", "trial"), ("/patents/", "patent")):
			with self.subTest(path=path):
				self.assertEqual(self.keys(path, noun, **params), sorted(expected))

	# --- dates ---

	def test_date_window_is_inclusive_of_both_whole_days(self):
		self.assert_all_endpoints(
			["tue_early", "tue_send", "mon_end"],
			discovery_date_after="2026-11-17",
			discovery_date_before="2026-11-23",
		)

	def test_after_date_starts_at_midnight(self):
		self.assert_all_endpoints(
			["tue_early", "tue_send", "mon_end", "tue_next"],
			discovery_date_after="2026-11-17",
		)

	def test_before_date_includes_the_whole_day(self):
		self.assert_all_endpoints(
			["mon_late", "tue_early", "tue_send", "mon_end"],
			discovery_date_before="2026-11-23",
		)

	def test_single_day(self):
		self.assert_all_endpoints(
			["tue_early", "tue_send"],
			discovery_date_after="2026-11-17",
			discovery_date_before="2026-11-17",
		)

	# --- datetimes ---

	def test_datetime_window_is_exact(self):
		# Send to send: from one Tuesday 08:05 up to the next Tuesday 08:00.
		self.assert_all_endpoints(
			["tue_send", "mon_end", "tue_next"],
			discovery_date_after="2026-11-17T08:05:00Z",
			discovery_date_before="2026-11-24T08:00:00Z",
		)

	def test_datetime_after_excludes_earlier_same_day(self):
		self.assert_all_endpoints(
			["tue_send", "mon_end", "tue_next"],
			discovery_date_after="2026-11-17T08:00:00Z",
		)

	def test_datetime_with_offset(self):
		# 09:05 in UTC+1 is 08:05 UTC.
		self.assert_all_endpoints(
			["tue_send", "mon_end", "tue_next"],
			discovery_date_after="2026-11-17T09:05:00+01:00",
		)

	def test_naive_datetime_is_read_in_server_time_zone(self):
		# TIME_ZONE is UTC, so a naive 08:05 is 08:05 UTC.
		self.assert_all_endpoints(
			["mon_late", "tue_early", "tue_send"],
			discovery_date_before="2026-11-17T08:05:00",
		)

	# --- composition and edges ---

	def test_null_discovery_date_is_excluded_by_any_bound(self):
		self.assertIn("undated", self.keys("/trials/", "trial"))
		self.assertNotIn("undated", self.keys("/trials/", "trial", discovery_date_after="2000-01-01"))
		self.assertNotIn("undated", self.keys("/trials/", "trial", discovery_date_before="2100-01-01"))

	def test_empty_value_is_ignored(self):
		self.assertEqual(
			len(self.keys("/articles/", "article", discovery_date_after="")), len(MOMENTS)
		)

	def test_composes_with_subject_and_ordering(self):
		self.assertEqual(
			self.keys(
				"/articles/",
				"article",
				subject_id=self.subject.pk,
				discovery_date_after="2026-11-17",
				discovery_date_before="2026-11-23",
				ordering="-discovery_date",
			),
			sorted(["tue_early", "tue_send", "mon_end"]),
		)

	def test_stats_honour_the_window(self):
		for path in ("/articles/stats/", "/trials/stats/", "/patents/stats/"):
			with self.subTest(path=path):
				response = self.client.get(
					path,
					{
						"site_id": self.site.pk,
						"discovery_date_after": "2026-11-17",
						"discovery_date_before": "2026-11-23",
					},
				)
				self.assertEqual(response.status_code, 200, response.content)
				self.assertEqual(response.data["total"], 3)

	def test_invalid_values_return_400(self):
		bad = ["2026-02-30", "2026-13-01", "yesterday", "17/11/2026", "2026-11-17T25:00:00Z"]
		for path in ("/articles/", "/trials/", "/patents/"):
			for value in bad:
				for param in ("discovery_date_after", "discovery_date_before"):
					with self.subTest(path=path, param=param, value=value):
						response = self.client.get(path, {"site_id": self.site.pk, param: value})
						self.assertEqual(response.status_code, 400, response.content)
						self.assertIn(param, response.data)
