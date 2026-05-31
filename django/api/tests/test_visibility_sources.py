"""
Tests for source visibility enforcement (PR 5).

Covers:
  - SourceViewSet list/detail: only sources whose team.org is visible
  - Detail endpoint 404s when source belongs to a hidden org
  - Four caller archetypes × the standard test matrix

Run with:
    docker exec gregory python manage.py test api.tests.test_visibility_sources
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.timezone import now
from organizations.models import Organization, OrganizationUser
from rest_framework.test import APIClient

from api.models import APIAccessScheme
from gregory.models import OrganizationApiSettings, Sources, Subject, Team

User = get_user_model()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_org(name, slug, public=False):
	org = Organization.objects.create(name=name, slug=slug)
	OrganizationApiSettings.objects.filter(organization=org).update(make_api_public=public)
	return org


def _make_team(org, name):
	slug = name.lower().replace(' ', '-')
	return Team.objects.create(organization=org, name=name, slug=slug)


def _make_subject(team, name):
	from django.utils.text import slugify
	return Subject.objects.create(team=team, subject_name=name, subject_slug=slugify(name))


def _make_source(team, subject, name):
	return Sources.objects.create(
		name=name,
		team=team,
		subject=subject,
		source_for='science paper',
		method='rss',
	)


def _make_api_scheme(org, name):
	return APIAccessScheme.objects.create(
		client_name=name,
		client_contacts=f'{name}@example.com',
		organization=org,
		ip_addresses='',
		begin_date=now() - timedelta(days=1),
		end_date=now() + timedelta(days=30),
	)


# ---------------------------------------------------------------------------
# Base setUp
# ---------------------------------------------------------------------------

class SourceVisibilityBase(TestCase):
	def setUp(self):
		self.my_org = _make_org('My Org', 'my-org-src', public=False)
		self.pub_org = _make_org('Public Org', 'pub-org-src', public=True)
		self.priv_org = _make_org('Private Org', 'priv-org-src', public=False)

		self.my_team = _make_team(self.my_org, 'My Team Src')
		self.pub_team = _make_team(self.pub_org, 'Pub Team Src')
		self.priv_team = _make_team(self.priv_org, 'Priv Team Src')

		self.my_subj = _make_subject(self.my_team, 'My Subj Src')
		self.pub_subj = _make_subject(self.pub_team, 'Pub Subj Src')
		self.priv_subj = _make_subject(self.priv_team, 'Priv Subj Src')

		self.src_mine = _make_source(self.my_team, self.my_subj, 'Mine Source')
		self.src_pub = _make_source(self.pub_team, self.pub_subj, 'Public Source')
		self.src_priv = _make_source(self.priv_team, self.priv_subj, 'Private Source')

		self.client = APIClient()


# ---------------------------------------------------------------------------
# Anonymous caller
# ---------------------------------------------------------------------------

class AnonymousSourceVisibilityTest(SourceVisibilityBase):
	def test_list_includes_public_source(self):
		resp = self.client.get('/sources/')
		self.assertEqual(resp.status_code, 200)
		ids = [s['source_id'] for s in resp.data['results']]
		self.assertIn(self.src_pub.source_id, ids)

	def test_list_excludes_private_sources(self):
		resp = self.client.get('/sources/')
		ids = [s['source_id'] for s in resp.data['results']]
		self.assertNotIn(self.src_mine.source_id, ids)
		self.assertNotIn(self.src_priv.source_id, ids)

	def test_detail_hidden_returns_404(self):
		resp = self.client.get(f'/sources/{self.src_mine.source_id}/')
		self.assertEqual(resp.status_code, 404)

	def test_detail_public_returns_200(self):
		resp = self.client.get(f'/sources/{self.src_pub.source_id}/')
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Authenticated user (member of my_org)
# ---------------------------------------------------------------------------

class AuthenticatedUserSourceVisibilityTest(SourceVisibilityBase):
	def setUp(self):
		super().setUp()
		self.user = User.objects.create_user(username='src-member', password='pw')
		OrganizationUser.objects.create(organization=self.my_org, user=self.user)
		self.client.force_login(self.user)

	def test_list_shows_own_org_source(self):
		resp = self.client.get('/sources/')
		ids = [s['source_id'] for s in resp.data['results']]
		self.assertIn(self.src_mine.source_id, ids)

	def test_list_excludes_unrelated_private_source(self):
		resp = self.client.get('/sources/')
		ids = [s['source_id'] for s in resp.data['results']]
		self.assertNotIn(self.src_priv.source_id, ids)

	def test_list_excludes_public_source_without_flag(self):
		resp = self.client.get('/sources/')
		ids = [s['source_id'] for s in resp.data['results']]
		self.assertNotIn(self.src_pub.source_id, ids)

	def test_include_public_adds_public_sources(self):
		resp = self.client.get('/sources/?include_public=true')
		ids = [s['source_id'] for s in resp.data['results']]
		self.assertIn(self.src_mine.source_id, ids)
		self.assertIn(self.src_pub.source_id, ids)
		self.assertNotIn(self.src_priv.source_id, ids)

	def test_detail_hidden_returns_404(self):
		resp = self.client.get(f'/sources/{self.src_priv.source_id}/')
		self.assertEqual(resp.status_code, 404)

	def test_detail_own_returns_200(self):
		resp = self.client.get(f'/sources/{self.src_mine.source_id}/')
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# API key caller (bound to my_org)
# ---------------------------------------------------------------------------

class APIKeySourceVisibilityTest(SourceVisibilityBase):
	def setUp(self):
		super().setUp()
		self.scheme = _make_api_scheme(self.my_org, 'src-key')
		self.client.credentials(HTTP_AUTHORIZATION=self.scheme.api_key)

	def test_list_shows_own_source(self):
		resp = self.client.get('/sources/')
		ids = [s['source_id'] for s in resp.data['results']]
		self.assertIn(self.src_mine.source_id, ids)

	def test_list_excludes_private_source(self):
		resp = self.client.get('/sources/')
		ids = [s['source_id'] for s in resp.data['results']]
		self.assertNotIn(self.src_priv.source_id, ids)

	def test_include_public_adds_public_sources(self):
		resp = self.client.get('/sources/?include_public=true')
		ids = [s['source_id'] for s in resp.data['results']]
		self.assertIn(self.src_mine.source_id, ids)
		self.assertIn(self.src_pub.source_id, ids)
		self.assertNotIn(self.src_priv.source_id, ids)

	def test_detail_hidden_returns_404(self):
		resp = self.client.get(f'/sources/{self.src_priv.source_id}/')
		self.assertEqual(resp.status_code, 404)

	def test_detail_own_returns_200(self):
		resp = self.client.get(f'/sources/{self.src_mine.source_id}/')
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Null-org API key (anonymous-equivalent)
# ---------------------------------------------------------------------------
