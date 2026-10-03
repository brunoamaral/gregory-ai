"""
Tests for ``?include=editorial`` and ``?has_takeaways`` (EDITORIAL-API-SPEC.md).

Editorial content (takeaways, summary_plain_english) is off by default, comes
back nested under ``editorial`` only on request, and which organisation's
content it is follows the caller's identity / site -- never ``?team_id=``.

Run with:
    docker exec gregory python manage.py test api.tests.test_editorial_include
"""

import csv
import io
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils.text import slugify
from django.utils.timezone import now
from organizations.models import Organization
from rest_framework.test import APIClient

from api.models import APIAccessScheme
from api.tests.visibility_helpers import private_site_publishing, publish_subjects
from gregory.models import (
	ArticleOrgContent,
	Articles,
	OrganizationApiSettings,
	Subject,
	Team,
	TrialOrgContent,
	Trials,
)

User = get_user_model()

PUBLIC_DOMAIN = "editorial-public.test.example.com"


def _org(name):
	return Organization.objects.create(name=name, slug=slugify(name))


def _subject(org, name):
	team = Team.objects.create(organization=org, name=f"{name} team", slug=slugify(f"{name} team"))
	return Subject.objects.create(team=team, subject_name=name, subject_slug=slugify(name))


def _key(org, site, name):
	return APIAccessScheme.objects.create(
		client_name=name,
		client_contacts=f"{name}@example.com",
		organization=org,
		site=site,
		ip_addresses="",
		begin_date=now() - timedelta(days=1),
		end_date=now() + timedelta(days=30),
	)


class EditorialFixtureMixin:
	"""Org A owns the one public site; org B owns a private site. Both
	subjects are on every record so every caller can see them."""

	@classmethod
	def setUpTestData(cls):
		cls.org_a = _org("Editorial Org A")
		cls.org_b = _org("Editorial Org B")
		cls.subject_a = _subject(cls.org_a, "Editorial Subject A")
		cls.subject_b = _subject(cls.org_b, "Editorial Subject B")
		cls.public_site = publish_subjects(
			cls.subject_a, cls.subject_b, organization=cls.org_a, domain=PUBLIC_DOMAIN
		)
		cls.private_site = private_site_publishing(
			cls.subject_a, cls.subject_b, organization=cls.org_b
		)
		cls.key_a = _key(cls.org_a, cls.public_site, "editorial-key-a")
		cls.key_b = _key(cls.org_b, cls.private_site, "editorial-key-b")

		cls.with_content = Articles.objects.create(title="With content", link="https://e.test/1")
		cls.without_content = Articles.objects.create(title="No content", link="https://e.test/2")
		cls.empty_takeaways = Articles.objects.create(title="Empty takeaways", link="https://e.test/3")
		for article in (cls.with_content, cls.without_content, cls.empty_takeaways):
			article.subjects.add(cls.subject_a, cls.subject_b)
			article.teams.add(cls.subject_a.team, cls.subject_b.team)
		ArticleOrgContent.objects.create(
			article=cls.with_content, organization=cls.org_a, takeaways="A takeaway"
		)
		ArticleOrgContent.objects.create(
			article=cls.with_content, organization=cls.org_b, takeaways="B takeaway",
			summary_plain_english="B plain",
		)
		ArticleOrgContent.objects.create(
			article=cls.empty_takeaways, organization=cls.org_a, takeaways=""
		)

		cls.trial = Trials.objects.create(title="Trial with content", link="https://t.test/1")
		cls.trial_bare = Trials.objects.create(title="Trial bare", link="https://t.test/2")
		for trial in (cls.trial, cls.trial_bare):
			trial.subjects.add(cls.subject_a, cls.subject_b)
			trial.teams.add(cls.subject_a.team, cls.subject_b.team)
		TrialOrgContent.objects.create(
			trial=cls.trial, organization=cls.org_a, takeaways="Trial A takeaway"
		)

	def anon(self, path, **extra):
		extra.setdefault("HTTP_ORIGIN", f"https://{PUBLIC_DOMAIN}")
		return APIClient().get(path, **extra)

	def keyed(self, key, path):
		return APIClient().get(path, HTTP_AUTHORIZATION=key.api_key)

	@staticmethod
	def by_title(resp, title):
		return next(r for r in resp.data["results"] if r["title"] == title)


class DefaultResponseTests(EditorialFixtureMixin, TestCase):
	def test_no_editorial_key_for_any_caller(self):
		user = User.objects.create_user(username="plain", password="x")
		self.org_a.add_user(user)
		session = APIClient()
		session.force_authenticate(user=user)
		for resp in (
			self.anon("/articles/"),
			self.keyed(self.key_a, "/articles/"),
			session.get("/articles/"),
		):
			self.assertEqual(resp.status_code, 200)
			row = self.by_title(resp, "With content")
			self.assertNotIn("editorial", row)
			self.assertNotIn("takeaways", row)
			self.assertNotIn("summary_plain_english", row)

	def test_team_id_no_longer_unlocks_anything(self):
		resp = self.anon(f"/articles/?team_id={self.subject_a.team_id}")
		row = self.by_title(resp, "With content")
		self.assertNotIn("takeaways", row)
		self.assertNotIn("editorial", row)

	def test_trials_default_has_no_editorial(self):
		row = self.by_title(self.anon("/trials/"), "Trial with content")
		self.assertNotIn("editorial", row)
		self.assertNotIn("takeaways", row)


class AnonymousIncludeTests(EditorialFixtureMixin, TestCase):
	def test_origin_resolves_the_sites_org(self):
		resp = self.anon("/articles/?include=editorial")
		self.assertEqual(resp.status_code, 200)
		row = self.by_title(resp, "With content")
		self.assertEqual(
			row["editorial"],
			[
				{
					"organization": {"id": self.org_a.id, "name": "Editorial Org A"},
					"takeaways": "A takeaway",
					"summary_plain_english": None,
				}
			],
		)

	def test_site_id_param_resolves_the_same_org(self):
		resp = APIClient().get(f"/articles/?include=editorial&site_id={self.public_site.id}")
		row = self.by_title(resp, "With content")
		self.assertEqual(row["editorial"][0]["organization"]["id"], self.org_a.id)

	def test_team_id_does_not_pick_the_org(self):
		# Org B's team asked for, but the site owner (org A) decides.
		resp = self.anon(f"/articles/?include=editorial&team_id={self.subject_b.team_id}")
		row = self.by_title(resp, "With content")
		self.assertEqual([e["organization"]["id"] for e in row["editorial"]], [self.org_a.id])

	def test_private_site_origin_never_yields_the_private_sites_org(self):
		resp = self.anon(
			"/articles/?include=editorial",
			HTTP_ORIGIN=f"https://{self.private_site.domain}",
		)
		self.assertEqual(resp.status_code, 200)
		row = self.by_title(resp, "With content")
		self.assertEqual(row["editorial"][0]["organization"]["id"], self.org_a.id)

	def test_record_without_content_row_has_null_fields(self):
		row = self.by_title(self.anon("/articles/?include=editorial"), "No content")
		self.assertEqual(len(row["editorial"]), 1)
		self.assertIsNone(row["editorial"][0]["takeaways"])
		self.assertIsNone(row["editorial"][0]["summary_plain_english"])

	def test_no_public_site_gives_empty_list(self):
		from sitesettings.models import CustomSetting

		CustomSetting.objects.filter(site=self.public_site).update(api_public=False)
		resp = APIClient().get("/articles/?include=editorial")
		self.assertEqual(resp.status_code, 200)
		for row in resp.data["results"]:
			self.assertEqual(row["editorial"], [])


class ApiKeyIncludeTests(EditorialFixtureMixin, TestCase):
	def test_returns_only_the_keys_org(self):
		row = self.by_title(self.keyed(self.key_b, "/articles/?include=editorial"), "With content")
		self.assertEqual(len(row["editorial"]), 1)
		entry = row["editorial"][0]
		self.assertEqual(entry["organization"]["id"], self.org_b.id)
		self.assertEqual(entry["takeaways"], "B takeaway")
		self.assertEqual(entry["summary_plain_english"], "B plain")

	def test_key_for_org_a_never_sees_org_b_content(self):
		row = self.by_title(self.keyed(self.key_a, "/articles/?include=editorial"), "With content")
		self.assertEqual([e["takeaways"] for e in row["editorial"]], ["A takeaway"])

	def test_detail_endpoint(self):
		resp = self.keyed(self.key_a, f"/articles/{self.with_content.article_id}/?include=editorial")
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data["editorial"][0]["takeaways"], "A takeaway")


class SessionUserIncludeTests(EditorialFixtureMixin, TestCase):
	def test_user_in_two_orgs_gets_two_entries_sorted_by_org_id(self):
		user = User.objects.create_user(username="both", password="x")
		self.org_b.add_user(user)
		self.org_a.add_user(user)
		client = APIClient()
		client.force_authenticate(user=user)
		resp = client.get("/articles/?include=editorial")
		row = self.by_title(resp, "No content")
		ids = [e["organization"]["id"] for e in row["editorial"]]
		self.assertEqual(ids, sorted([self.org_a.id, self.org_b.id]))
		row = self.by_title(resp, "With content")
		by_org = {e["organization"]["id"]: e for e in row["editorial"]}
		self.assertEqual(by_org[self.org_a.id]["takeaways"], "A takeaway")
		self.assertEqual(by_org[self.org_b.id]["takeaways"], "B takeaway")

	def test_user_in_no_org_gets_empty_list(self):
		user = User.objects.create_user(username="orphan", password="x")
		client = APIClient()
		client.force_authenticate(user=user)
		resp = client.get("/articles/?include=editorial")
		self.assertEqual(resp.status_code, 200)
		for row in resp.data["results"]:
			self.assertEqual(row["editorial"], [])


class IncludeValidationTests(EditorialFixtureMixin, TestCase):
	def test_unknown_value_is_400_and_lists_accepted(self):
		resp = self.anon("/articles/?include=editorial,bogus")
		self.assertEqual(resp.status_code, 400)
		self.assertIn("bogus", str(resp.data))
		self.assertIn("editorial", str(resp.data))

	def test_unknown_value_is_400_on_trials_too(self):
		self.assertEqual(self.anon("/trials/?include=nope").status_code, 400)

	def test_comma_list_and_case_are_tolerated(self):
		resp = self.anon("/articles/?include=Editorial, editorial")
		self.assertEqual(resp.status_code, 200)
		self.assertIn("editorial", self.by_title(resp, "With content"))


class HasTakeawaysTests(EditorialFixtureMixin, TestCase):
	def titles(self, resp):
		self.assertEqual(resp.status_code, 200)
		return sorted(r["title"] for r in resp.data["results"])

	def test_true_matches_only_non_empty_takeaways_of_callers_org(self):
		self.assertEqual(self.titles(self.anon("/articles/?has_takeaways=true")), ["With content"])

	def test_false_is_everything_else_with_empty_string_counting_as_missing(self):
		self.assertEqual(
			self.titles(self.anon("/articles/?has_takeaways=false")),
			["Empty takeaways", "No content"],
		)

	def test_follows_the_callers_org(self):
		# Org B only has content on "With content" too, but via its own row.
		resp = self.keyed(self.key_b, "/articles/?has_takeaways=true")
		self.assertEqual(self.titles(resp), ["With content"])

	def test_does_not_need_include_and_does_not_add_editorial(self):
		resp = self.anon("/articles/?has_takeaways=true")
		self.assertNotIn("editorial", resp.data["results"][0])

	def test_no_editorial_orgs_true_is_empty_false_is_everything(self):
		from sitesettings.models import CustomSetting

		CustomSetting.objects.filter(site=self.public_site).update(api_public=False)
		client = APIClient()
		self.assertEqual(client.get("/articles/?has_takeaways=true").data["count"], 0)
		self.assertEqual(client.get("/articles/?has_takeaways=false").data["count"], 0)

	def test_no_duplicate_rows_with_multiple_orgs(self):
		user = User.objects.create_user(username="dup", password="x")
		self.org_a.add_user(user)
		self.org_b.add_user(user)
		client = APIClient()
		client.force_authenticate(user=user)
		resp = client.get("/articles/?has_takeaways=true")
		self.assertEqual([r["title"] for r in resp.data["results"]], ["With content"])

	def test_trials(self):
		resp = self.anon("/trials/?has_takeaways=true")
		self.assertEqual([r["title"] for r in resp.data["results"]], ["Trial with content"])
		resp = self.anon("/trials/?has_takeaways=false")
		self.assertEqual([r["title"] for r in resp.data["results"]], ["Trial bare"])


class TrialIncludeTests(EditorialFixtureMixin, TestCase):
	def test_list_and_detail(self):
		resp = self.anon("/trials/?include=editorial")
		row = self.by_title(resp, "Trial with content")
		self.assertEqual(row["editorial"][0]["takeaways"], "Trial A takeaway")
		row = self.by_title(resp, "Trial bare")
		self.assertIsNone(row["editorial"][0]["takeaways"])
		detail = self.anon(f"/trials/{self.trial.trial_id}/?include=editorial")
		self.assertEqual(detail.status_code, 200)
		self.assertEqual(detail.data["editorial"][0]["takeaways"], "Trial A takeaway")


class SearchViewTests(EditorialFixtureMixin, TestCase):
	def setUp(self):
		# The search views validate team_id against visible organisations,
		# which for an anonymous caller still means make_api_public.
		OrganizationApiSettings.objects.update_or_create(
			organization=self.org_a, defaults={"make_api_public": True}
		)

	def test_article_search_get_and_post(self):
		base = {"team_id": self.subject_a.team_id, "subject_id": self.subject_a.id}
		resp = self.anon(
			f"/articles/search/?team_id={base['team_id']}&subject_id={base['subject_id']}"
			"&include=editorial"
		)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(
			self.by_title(resp, "With content")["editorial"][0]["takeaways"], "A takeaway"
		)
		resp = APIClient().post(
			"/articles/search/",
			{**base, "include": "editorial", "has_takeaways": True},
			format="json",
			HTTP_ORIGIN=f"https://{PUBLIC_DOMAIN}",
		)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual([r["title"] for r in resp.data["results"]], ["With content"])
		self.assertEqual(resp.data["results"][0]["editorial"][0]["takeaways"], "A takeaway")

	def test_trial_search(self):
		url = (
			f"/trials/search/?team_id={self.subject_a.team_id}"
			f"&subject_id={self.subject_a.id}&include=editorial"
		)
		resp = self.anon(url)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(
			self.by_title(resp, "Trial with content")["editorial"][0]["takeaways"],
			"Trial A takeaway",
		)


class CsvTests(EditorialFixtureMixin, TestCase):
	def test_csv_never_has_editorial_columns(self):
		for extra in ("", "&include=editorial"):
			resp = self.anon(f"/articles/?format=csv&all_results=true{extra}")
			self.assertEqual(resp.status_code, 200)
			content = b"".join(resp.streaming_content).decode("utf-8")
			rows = list(csv.reader(io.StringIO(content)))
			header = rows[0]
			for column in ("editorial", "takeaways", "summary_plain_english"):
				self.assertNotIn(column, header)
			for row in rows[1:]:
				self.assertEqual(len(row), len(header))

	def test_paginated_csv_has_no_editorial_either(self):
		resp = self.anon("/articles/?format=csv&include=editorial")
		self.assertEqual(resp.status_code, 200)
		body = b"".join(resp.streaming_content) if resp.streaming else resp.content
		header = next(csv.reader(io.StringIO(body.decode("utf-8"))))
		self.assertNotIn("editorial", header)


class QueryCountTests(EditorialFixtureMixin, TestCase):
	def _count(self, path):
		with CaptureQueriesContext(connection) as ctx:
			resp = self.anon(path)
		self.assertEqual(resp.status_code, 200)
		return len(ctx)

	def test_constant_queries_regardless_of_page_size(self):
		for i in range(8):
			article = Articles.objects.create(title=f"Bulk {i}", link=f"https://bulk.test/{i}")
			article.subjects.add(self.subject_a)
			ArticleOrgContent.objects.create(
				article=article, organization=self.org_a, takeaways=f"t{i}"
			)
		self._count("/articles/?include=editorial&page_size=2")  # warm caches
		small = self._count("/articles/?include=editorial&page_size=2")
		large = self._count("/articles/?include=editorial&page_size=10")
		self.assertEqual(small, large)

	def test_default_path_does_not_prefetch_org_contents(self):
		with CaptureQueriesContext(connection) as ctx:
			self.anon("/articles/")
		self.assertFalse(
			any("gregory_articleorgcontent" in q["sql"] for q in ctx.captured_queries)
		)
