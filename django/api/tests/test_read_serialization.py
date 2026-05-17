"""
Tests for org-scoped takeaways read serialization.

Verify ``ArticleSerializer.get_takeaways`` / ``get_summary_plain_english``
and the omission behaviour added by ``OrgScopedSerializerMixin``.

Covers spec §10.3:
  - With API key for Org A: takeaways resolves to Org A's ArticleOrgContent
  - With API key for Org A, no Org A content exists: takeaways is null
  - Anonymous (no API key): takeaways field is absent from response
  - Public-org ?team_id path: takeaways present if make_api_public=True
  - Two orgs editing same article: each sees its own takeaways

Run with:
    docker exec gregory python manage.py test api.tests.test_read_serialization
"""
from datetime import timedelta

from django.test import TestCase
from django.utils.timezone import now
from organizations.models import Organization
from rest_framework.test import APIClient

from api.models import APIAccessScheme
from gregory.models import (
	Articles, ArticleOrgContent, Team, Subject,
	OrganizationApiSettings,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_org(name, slug='', public=False):
	slug = slug or name.lower().replace(' ', '-')
	org = Organization.objects.create(name=name, slug=slug)
	OrganizationApiSettings.objects.filter(organization=org).update(make_api_public=public)
	return org


def _make_team(org, name):
	return Team.objects.create(organization=org, name=name, slug=name.lower().replace(' ', '-'))


def _make_subject(team, name):
	from django.utils.text import slugify
	return Subject.objects.create(team=team, subject_name=name, subject_slug=slugify(name))


def _make_scheme(org, name):
	return APIAccessScheme.objects.create(
		client_name=name,
		client_contacts=f'{name}@example.com',
		organization=org,
		ip_addresses='',
		begin_date=now() - timedelta(days=1),
		end_date=now() + timedelta(days=30),
	)


def _make_article(team, title='Test Article', link='https://example.com/art1'):
	article = Articles.objects.create(title=title, link=link)
	article.teams.add(team)
	return article


def _api_get(client, path, api_key=None):
	headers = {}
	if api_key:
		headers['HTTP_AUTHORIZATION'] = api_key
	return client.get(path, **headers)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TakeawaysReadWithApiKeyTest(TestCase):
	"""
	Org-A API key → response contains takeaways from ArticleOrgContent (Phase 4).
	"""

	def setUp(self):
		self.client = APIClient()
		self.org = _make_org('Read Org A')
		self.team = _make_team(self.org, 'Read Team A')
		self.scheme = _make_scheme(self.org, 'read-key-a')
		self.article = _make_article(self.team)

	def test_takeaways_from_org_content_when_key_present(self):
		"""API key for Org A → takeaways resolves to ArticleOrgContent for Org A."""
		ArticleOrgContent.objects.create(
			article=self.article,
			organization=self.org,
			takeaways='My org takeaway',
		)
		resp = _api_get(self.client, '/articles/', self.scheme.api_key)
		self.assertEqual(resp.status_code, 200)
		results = resp.data['results']
		article_data = next(a for a in results if a['article_id'] == self.article.article_id)
		self.assertEqual(article_data['takeaways'], 'My org takeaway')

	def test_takeaways_null_when_no_org_content(self):
		"""API key for Org A, no ArticleOrgContent row → takeaways is null."""
		resp = _api_get(self.client, '/articles/', self.scheme.api_key)
		self.assertEqual(resp.status_code, 200)
		results = resp.data['results']
		article_data = next(a for a in results if a['article_id'] == self.article.article_id)
		self.assertIsNone(article_data.get('takeaways'))

	def test_detail_endpoint_takeaways(self):
		"""Detail endpoint also returns org-scoped takeaways."""
		ArticleOrgContent.objects.create(
			article=self.article,
			organization=self.org,
			takeaways='Detail takeaway',
		)
		resp = _api_get(self.client, f'/articles/{self.article.article_id}/', self.scheme.api_key)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data['takeaways'], 'Detail takeaway')


class TakeawaysReadAnonymousTest(TestCase):
	"""Anonymous request → takeaways field absent from response."""

	def setUp(self):
		self.client = APIClient()
		self.org = _make_org('Anon Org', public=True)
		self.team = _make_team(self.org, 'Anon Team')
		self.article = _make_article(self.team, title='Anon Article', link='https://example.com/anon')
		ArticleOrgContent.objects.create(
			article=self.article,
			organization=self.org,
			takeaways='Should not be visible anonymously',
		)

	def test_takeaways_absent_for_anonymous(self):
		"""Anonymous request: takeaways should not appear in the response."""
		resp = self.client.get('/articles/')
		self.assertEqual(resp.status_code, 200)
		results = resp.data['results']
		anon_articles = [a for a in results if a['article_id'] == self.article.article_id]
		self.assertEqual(len(anon_articles), 1)
		self.assertNotIn('takeaways', anon_articles[0])


class TakeawaysPublicOrgTeamIdTest(TestCase):
	"""
	?team_id of a make_api_public=True org → takeaways present.

	Note: this test checks Phase 4 behaviour where _resolve_per_org_fields_org
	falls back to the team's org when make_api_public=True.
	"""

	def setUp(self):
		self.client = APIClient()
		self.org = _make_org('Public Org TK', public=True)
		self.team = _make_team(self.org, 'Public Team TK')
		self.article = _make_article(self.team, title='Public TK Article', link='https://example.com/pbtk')
		ArticleOrgContent.objects.create(
			article=self.article,
			organization=self.org,
			takeaways='Public org takeaway',
		)

	def test_takeaways_present_for_public_org_via_team_id(self):
		resp = self.client.get(f'/articles/?team_id={self.team.id}')
		self.assertEqual(resp.status_code, 200)
		results = resp.data['results']
		article_data = next((a for a in results if a['article_id'] == self.article.article_id), None)
		self.assertIsNotNone(article_data)
		self.assertEqual(article_data.get('takeaways'), 'Public org takeaway')


class TakeawaysTwoOrgsTest(TestCase):
	"""
	Two orgs each have ArticleOrgContent for the same article.
	Each should see only its own takeaways.
	"""

	def setUp(self):
		self.client = APIClient()
		self.org_a = _make_org('Dual Org A')
		self.org_b = _make_org('Dual Org B', 'dual-org-b')
		self.team_a = _make_team(self.org_a, 'Dual Team A')
		self.team_b = _make_team(self.org_b, 'Dual Team B')
		self.scheme_a = _make_scheme(self.org_a, 'dual-key-a')
		self.scheme_b = _make_scheme(self.org_b, 'dual-key-b')
		# Article shared across both orgs
		self.article = Articles.objects.create(
			title='Shared Article',
			link='https://example.com/shared',
		)
		self.article.teams.add(self.team_a, self.team_b)
		ArticleOrgContent.objects.create(
			article=self.article, organization=self.org_a, takeaways='Org A takeaway'
		)
		ArticleOrgContent.objects.create(
			article=self.article, organization=self.org_b, takeaways='Org B takeaway'
		)

	def test_org_a_sees_own_takeaway(self):
		resp = _api_get(self.client, f'/articles/{self.article.article_id}/', self.scheme_a.api_key)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data['takeaways'], 'Org A takeaway')

	def test_org_b_sees_own_takeaway(self):
		resp = _api_get(self.client, f'/articles/{self.article.article_id}/', self.scheme_b.api_key)
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.data['takeaways'], 'Org B takeaway')
