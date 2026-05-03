"""
Tests for organisation visibility enforcement (PR 5).

Covers:
  - OrganizationsViewSet list/detail: only orgs in visible_org_ids
  - Detail endpoint 404s when org is not visible (hide-existence rule)
  - Four caller archetypes × the standard test matrix

Run with:
    docker exec gregory python manage.py test api.tests.test_visibility_organisations
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.timezone import now
from organizations.models import Organization, OrganizationUser
from rest_framework.test import APIClient

from api.models import APIAccessScheme
from gregory.models import OrganizationApiSettings, Team

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

class OrganizationVisibilityBase(TestCase):
	def setUp(self):
		self.my_org = _make_org('My Org', 'my-org-orgs', public=False)
		self.pub_org = _make_org('Public Org', 'pub-org-orgs', public=True)
		self.priv_org = _make_org('Private Org', 'priv-org-orgs', public=False)

		# Teams are needed so visible_org_ids works via OrganizationUser membership
		_make_team(self.my_org, 'My Team Orgs')
		_make_team(self.pub_org, 'Pub Team Orgs')
		_make_team(self.priv_org, 'Priv Team Orgs')

		self.client = APIClient()


# ---------------------------------------------------------------------------
# Anonymous caller
# ---------------------------------------------------------------------------

class AnonymousOrganizationVisibilityTest(OrganizationVisibilityBase):
	def test_list_includes_public_org(self):
		resp = self.client.get('/organizations/')
		self.assertEqual(resp.status_code, 200)
		ids = [o['id'] for o in resp.data['results']]
		self.assertIn(self.pub_org.id, ids)

	def test_list_excludes_private_orgs(self):
		resp = self.client.get('/organizations/')
		ids = [o['id'] for o in resp.data['results']]
		self.assertNotIn(self.my_org.id, ids)
		self.assertNotIn(self.priv_org.id, ids)

	def test_detail_hidden_returns_404(self):
		resp = self.client.get(f'/organizations/{self.my_org.id}/')
		self.assertEqual(resp.status_code, 404)

	def test_detail_public_returns_200(self):
		resp = self.client.get(f'/organizations/{self.pub_org.id}/')
		self.assertEqual(resp.status_code, 200)

	def test_include_public_is_noop_for_anonymous(self):
		resp_plain = self.client.get('/organizations/')
		resp_flag = self.client.get('/organizations/?include_public=true')
		plain_ids = {o['id'] for o in resp_plain.data['results']}
		flag_ids = {o['id'] for o in resp_flag.data['results']}
		self.assertEqual(plain_ids, flag_ids)


# ---------------------------------------------------------------------------
# Authenticated user (member of my_org)
# ---------------------------------------------------------------------------

class AuthenticatedUserOrganizationVisibilityTest(OrganizationVisibilityBase):
	def setUp(self):
		super().setUp()
		self.user = User.objects.create_user(username='org-member', password='pw')
		OrganizationUser.objects.create(organization=self.my_org, user=self.user)
		self.client.force_login(self.user)

	def test_list_shows_own_org(self):
		resp = self.client.get('/organizations/')
		ids = [o['id'] for o in resp.data['results']]
		self.assertIn(self.my_org.id, ids)

	def test_list_excludes_unrelated_private_org(self):
		resp = self.client.get('/organizations/')
		ids = [o['id'] for o in resp.data['results']]
		self.assertNotIn(self.priv_org.id, ids)

	def test_list_excludes_public_org_without_flag(self):
		resp = self.client.get('/organizations/')
		ids = [o['id'] for o in resp.data['results']]
		self.assertNotIn(self.pub_org.id, ids)

	def test_include_public_adds_public_org(self):
		resp = self.client.get('/organizations/?include_public=true')
		ids = [o['id'] for o in resp.data['results']]
		self.assertIn(self.my_org.id, ids)
		self.assertIn(self.pub_org.id, ids)
		self.assertNotIn(self.priv_org.id, ids)

	def test_detail_hidden_returns_404(self):
		resp = self.client.get(f'/organizations/{self.priv_org.id}/')
		self.assertEqual(resp.status_code, 404)

	def test_detail_own_returns_200(self):
		resp = self.client.get(f'/organizations/{self.my_org.id}/')
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# API key caller (bound to my_org)
# ---------------------------------------------------------------------------

class APIKeyOrganizationVisibilityTest(OrganizationVisibilityBase):
	def setUp(self):
		super().setUp()
		self.scheme = _make_api_scheme(self.my_org, 'org-key')
		self.client.credentials(HTTP_AUTHORIZATION=self.scheme.api_key)

	def test_list_shows_own_org(self):
		resp = self.client.get('/organizations/')
		ids = [o['id'] for o in resp.data['results']]
		self.assertIn(self.my_org.id, ids)

	def test_list_excludes_other_private_org(self):
		resp = self.client.get('/organizations/')
		ids = [o['id'] for o in resp.data['results']]
		self.assertNotIn(self.priv_org.id, ids)

	def test_list_excludes_public_org_without_flag(self):
		resp = self.client.get('/organizations/')
		ids = [o['id'] for o in resp.data['results']]
		self.assertNotIn(self.pub_org.id, ids)

	def test_include_public_adds_public_org(self):
		resp = self.client.get('/organizations/?include_public=true')
		ids = [o['id'] for o in resp.data['results']]
		self.assertIn(self.my_org.id, ids)
		self.assertIn(self.pub_org.id, ids)
		self.assertNotIn(self.priv_org.id, ids)

	def test_detail_hidden_returns_404(self):
		resp = self.client.get(f'/organizations/{self.priv_org.id}/')
		self.assertEqual(resp.status_code, 404)

	def test_detail_own_returns_200(self):
		resp = self.client.get(f'/organizations/{self.my_org.id}/')
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Null-org API key (anonymous-equivalent)
# ---------------------------------------------------------------------------

class NullOrgAPIKeyOrganizationVisibilityTest(OrganizationVisibilityBase):
	def setUp(self):
		super().setUp()
		self.scheme = APIAccessScheme.objects.create(
			client_name='null-org-org-key',
			client_contacts='null-org@example.com',
			organization=None,
			ip_addresses='',
			begin_date=now() - timedelta(days=1),
			end_date=now() + timedelta(days=30),
		)
		self.client.credentials(HTTP_AUTHORIZATION=self.scheme.api_key)

	def test_list_shows_only_public_orgs(self):
		resp = self.client.get('/organizations/')
		ids = [o['id'] for o in resp.data['results']]
		self.assertIn(self.pub_org.id, ids)
		self.assertNotIn(self.my_org.id, ids)
		self.assertNotIn(self.priv_org.id, ids)

	def test_detail_of_private_org_returns_404(self):
		resp = self.client.get(f'/organizations/{self.my_org.id}/')
		self.assertEqual(resp.status_code, 404)
