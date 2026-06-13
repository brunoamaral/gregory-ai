"""
Tests for POST /articles/edit/

Covers spec §10.1:
  - Successful upsert (no prior ArticleOrgContent row)
  - Successful update of existing ArticleOrgContent
  - Per-article fields (access, retracted, kind) persist on Articles
  - Article not found by DOI → 404
  - Multiple articles match DOI → 409 DuplicateArticleError with ids; no write
  - Article belongs to different org → 403 CrossOrgPayloadError
  - Invalid access / kind values → 400
  - Missing API key → 401
  - API key without org → 403
  - Failed edits create APIAccessSchemeLog row with correct http_code
  - Empty string takeaways clears the field (saved as NULL)

Run with:
    docker exec gregory python manage.py test api.tests.test_edit_article
"""

import json
from datetime import timedelta

from django.test import TestCase, Client
from django.utils.timezone import now
from organizations.models import Organization

from api.models import APIAccessScheme, APIAccessSchemeLog
from gregory.models import (
	Articles,
	ArticleOrgContent,
	Sources,
	Team,
	Subject,
	OrganizationApiSettings,
)

# ---------------------------------------------------------------------------
# Shared helpers (duplicated from test_post_article_org_scoping pattern)
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


def _make_subject(team, name):
	from django.utils.text import slugify

	return Subject.objects.create(
		team=team, subject_name=name, subject_slug=slugify(name)
	)


def _make_source(team, subject, name):
	return Sources.objects.create(
		name=name,
		link=f"https://src.example.com/{name}",
		team=team,
		subject=subject,
		source_for="science paper",
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


def _make_article(team, doi="10.1234/test", title="Test Article"):
	article = Articles.objects.create(
		title=title,
		link=f"https://example.com/{doi}",
		doi=doi,
	)
	article.teams.add(team)
	return article


def _edit(client, api_key, payload):
	body = json.dumps(payload).encode()
	headers = {"HTTP_AUTHORIZATION": api_key} if api_key else {}
	return client.post(
		"/articles/edit/",
		data=body,
		content_type="application/json",
		**headers,
	)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class EditArticleOrgContentTest(TestCase):
	"""Per-org fields (takeaways, summary_plain_english)."""

	def setUp(self):
		self.client = Client()
		self.org = _make_org("Org A")
		self.team = _make_team(self.org, "Team A")
		self.subject = _make_subject(self.team, "Subject A")
		self.scheme = _make_scheme(self.org, "key-a")
		self.article = _make_article(self.team, doi="10.1111/upsert")

	def test_upsert_creates_new_row(self):
		"""No prior ArticleOrgContent → row is created."""
		resp = _edit(
			self.client,
			self.scheme.api_key,
			{
				"doi": "10.1111/upsert",
				"takeaways": "First takeaway",
			},
		)
		self.assertEqual(resp.status_code, 200)
		data = resp.json()
		self.assertIn("takeaways", data["updated_fields"])
		content = ArticleOrgContent.objects.get(
			article=self.article, organization=self.org
		)
		self.assertEqual(content.takeaways, "First takeaway")

	def test_upsert_updates_existing_row(self):
		"""Existing ArticleOrgContent row is updated in place."""
		ArticleOrgContent.objects.create(
			article=self.article, organization=self.org, takeaways="Old value"
		)
		resp = _edit(
			self.client,
			self.scheme.api_key,
			{
				"doi": "10.1111/upsert",
				"takeaways": "New value",
			},
		)
		self.assertEqual(resp.status_code, 200)
		content = ArticleOrgContent.objects.get(
			article=self.article, organization=self.org
		)
		self.assertEqual(content.takeaways, "New value")
		self.assertEqual(
			ArticleOrgContent.objects.filter(
				article=self.article, organization=self.org
			).count(),
			1,
		)

	def test_empty_string_clears_takeaways(self):
		"""Empty string takeaways is stored as NULL."""
		ArticleOrgContent.objects.create(
			article=self.article, organization=self.org, takeaways="Something"
		)
		resp = _edit(
			self.client,
			self.scheme.api_key,
			{
				"doi": "10.1111/upsert",
				"takeaways": "",
			},
		)
		self.assertEqual(resp.status_code, 200)
		content = ArticleOrgContent.objects.get(
			article=self.article, organization=self.org
		)
		self.assertIsNone(content.takeaways)

	def test_omitted_field_not_changed(self):
		"""Fields absent from the payload are not modified."""
		ArticleOrgContent.objects.create(
			article=self.article, organization=self.org, takeaways="Keep me"
		)
		resp = _edit(
			self.client,
			self.scheme.api_key,
			{
				"doi": "10.1111/upsert",
				"summary_plain_english": "A summary",
			},
		)
		self.assertEqual(resp.status_code, 200)
		content = ArticleOrgContent.objects.get(
			article=self.article, organization=self.org
		)
		self.assertEqual(content.takeaways, "Keep me")
		self.assertEqual(content.summary_plain_english, "A summary")


class EditArticlePerArticleFieldsTest(TestCase):
	"""Per-article fields (access, retracted, kind)."""

	def setUp(self):
		self.client = Client()
		self.org = _make_org("Org B")
		self.team = _make_team(self.org, "Team B")
		self.scheme = _make_scheme(self.org, "key-b")
		self.article = _make_article(self.team, doi="10.2222/fields")

	def test_access_persists(self):
		resp = _edit(
			self.client,
			self.scheme.api_key,
			{
				"doi": "10.2222/fields",
				"access": "open",
			},
		)
		self.assertEqual(resp.status_code, 200)
		self.article.refresh_from_db()
		self.assertEqual(self.article.access, "open")

	def test_retracted_persists(self):
		resp = _edit(
			self.client,
			self.scheme.api_key,
			{
				"doi": "10.2222/fields",
				"retracted": True,
			},
		)
		self.assertEqual(resp.status_code, 200)
		self.article.refresh_from_db()
		self.assertTrue(self.article.retracted)

	def test_kind_persists(self):
		resp = _edit(
			self.client,
			self.scheme.api_key,
			{
				"doi": "10.2222/fields",
				"kind": "news article",
			},
		)
		self.assertEqual(resp.status_code, 200)
		self.article.refresh_from_db()
		self.assertEqual(self.article.kind, "news article")

	def test_invalid_access_returns_400(self):
		resp = _edit(
			self.client,
			self.scheme.api_key,
			{
				"doi": "10.2222/fields",
				"access": "INVALID",
			},
		)
		self.assertEqual(resp.status_code, 400)

	def test_invalid_kind_returns_400(self):
		resp = _edit(
			self.client,
			self.scheme.api_key,
			{
				"doi": "10.2222/fields",
				"kind": "not-a-kind",
			},
		)
		self.assertEqual(resp.status_code, 400)

	def test_retracted_non_bool_returns_400(self):
		resp = _edit(
			self.client,
			self.scheme.api_key,
			{
				"doi": "10.2222/fields",
				"retracted": "yes",
			},
		)
		self.assertEqual(resp.status_code, 400)


class EditArticleErrorsTest(TestCase):
	"""Error paths: 401, 403, 404, 409."""

	def setUp(self):
		self.client = Client()
		self.org = _make_org("Org C")
		self.other_org = _make_org("Org D", "org-d")
		self.team = _make_team(self.org, "Team C")
		self.other_team = _make_team(self.other_org, "Team D")
		self.scheme = _make_scheme(self.org, "key-c")
		self.article = _make_article(self.team, doi="10.3333/errors")

	def test_missing_api_key_returns_401(self):
		resp = _edit(self.client, None, {"doi": "10.3333/errors"})
		self.assertEqual(resp.status_code, 401)

	def test_invalid_api_key_returns_401(self):
		resp = _edit(self.client, "not-a-real-key", {"doi": "10.3333/errors"})
		self.assertEqual(resp.status_code, 401)

	def test_article_not_found_returns_404(self):
		resp = _edit(self.client, self.scheme.api_key, {"doi": "10.9999/nonexistent"})
		self.assertEqual(resp.status_code, 404)

	def test_duplicate_doi_returns_409_with_ids(self):
		# Create a second article with the same DOI
		duplicate = Articles.objects.create(
			title="Duplicate", link="https://example.com/dup2", doi="10.3333/errors"
		)
		duplicate.teams.add(self.team)
		resp = _edit(self.client, self.scheme.api_key, {"doi": "10.3333/errors"})
		self.assertEqual(resp.status_code, 409)
		data = resp.json()
		self.assertIn("article_ids", data["extra_data"])
		self.assertIn(self.article.article_id, data["extra_data"]["article_ids"])
		self.assertIn(duplicate.article_id, data["extra_data"]["article_ids"])
		# No writes should have occurred
		self.assertEqual(ArticleOrgContent.objects.count(), 0)

	def test_cross_org_article_returns_403(self):
		"""Article belongs to other_org only → 403."""
		other_article = _make_article(
			self.other_team, doi="10.4444/other", title="Cross Org Article"
		)
		resp = _edit(
			self.client,
			self.scheme.api_key,
			{
				"doi": "10.4444/other",
				"takeaways": "Should not be written",
			},
		)
		self.assertEqual(resp.status_code, 403)
		self.assertEqual(ArticleOrgContent.objects.count(), 0)


class EditArticleAuditLogTest(TestCase):
	"""APIAccessSchemeLog rows are created on success and on error."""

	def setUp(self):
		self.client = Client()
		self.org = _make_org("Org E")
		self.team = _make_team(self.org, "Team E")
		self.scheme = _make_scheme(self.org, "key-e")
		self.article = _make_article(self.team, doi="10.5555/log")

	def test_success_creates_log_with_200(self):
		_edit(
			self.client,
			self.scheme.api_key,
			{
				"doi": "10.5555/log",
				"takeaways": "logged",
			},
		)
		log = APIAccessSchemeLog.objects.filter(api_access_scheme=self.scheme).latest(
			"access_date"
		)
		self.assertEqual(log.http_code, 200)

	def test_404_creates_log(self):
		_edit(self.client, self.scheme.api_key, {"doi": "10.9999/no"})
		log = APIAccessSchemeLog.objects.filter(api_access_scheme=self.scheme).latest(
			"access_date"
		)
		self.assertEqual(log.http_code, 404)

	def test_403_cross_org_creates_log(self):
		other_org = _make_org("Org F", "org-f")
		other_team = _make_team(other_org, "Team F")
		_make_article(other_team, doi="10.6666/forbidden", title="Forbidden Article")
		_edit(self.client, self.scheme.api_key, {"doi": "10.6666/forbidden"})
		log = APIAccessSchemeLog.objects.filter(api_access_scheme=self.scheme).latest(
			"access_date"
		)
		self.assertEqual(log.http_code, 403)
