"""
Tests for team visibility enforcement (PR 5).

Covers:
  - TeamsViewSet list/detail: only teams in visible orgs
  - Detail endpoint 404s when team belongs to a hidden org
  - Permission changed from IsAuthenticated → IsAuthenticatedOrReadOnly
    (anonymous users can now see public orgs' teams without authentication)
  - Four caller archetypes × the standard test matrix

Run with:
    docker exec gregory python manage.py test api.tests.test_visibility_teams
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
	OrganizationApiSettings.objects.filter(organization=org).update(
		make_api_public=public
	)
	return org


def _make_team(org, name):
	slug = name.lower().replace(" ", "-")
	return Team.objects.create(organization=org, name=name, slug=slug)


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


class TeamVisibilityBase(TestCase):
	def setUp(self):
		self.my_org = _make_org("My Org", "my-org-team", public=False)
		self.pub_org = _make_org("Public Org", "pub-org-team", public=True)
		self.priv_org = _make_org("Private Org", "priv-org-team", public=False)

		self.my_team = _make_team(self.my_org, "My Team Teams")
		self.pub_team = _make_team(self.pub_org, "Pub Team Teams")
		self.priv_team = _make_team(self.priv_org, "Priv Team Teams")

		self.client = APIClient()


# ---------------------------------------------------------------------------
# Anonymous caller
# ---------------------------------------------------------------------------


class AnonymousTeamVisibilityTest(TeamVisibilityBase):
	def test_list_does_not_require_auth(self):
		"""TeamsViewSet is now IsAuthenticatedOrReadOnly — anonymous read is allowed."""
		resp = self.client.get("/teams/")
		self.assertEqual(resp.status_code, 200)

	def test_list_includes_public_team(self):
		resp = self.client.get("/teams/")
		ids = [t["id"] for t in resp.data["results"]]
		self.assertIn(self.pub_team.id, ids)

	def test_list_excludes_private_teams(self):
		resp = self.client.get("/teams/")
		ids = [t["id"] for t in resp.data["results"]]
		self.assertNotIn(self.my_team.id, ids)
		self.assertNotIn(self.priv_team.id, ids)

	def test_detail_hidden_returns_404(self):
		resp = self.client.get(f"/teams/{self.my_team.id}/")
		self.assertEqual(resp.status_code, 404)

	def test_detail_public_returns_200(self):
		resp = self.client.get(f"/teams/{self.pub_team.id}/")
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Authenticated user (member of my_org)
# ---------------------------------------------------------------------------


class AuthenticatedUserTeamVisibilityTest(TeamVisibilityBase):
	def setUp(self):
		super().setUp()
		self.user = User.objects.create_user(username="team-member", password="pw")
		OrganizationUser.objects.create(organization=self.my_org, user=self.user)
		self.client.force_login(self.user)

	def test_list_shows_own_team(self):
		resp = self.client.get("/teams/")
		ids = [t["id"] for t in resp.data["results"]]
		self.assertIn(self.my_team.id, ids)

	def test_list_excludes_unrelated_private_team(self):
		resp = self.client.get("/teams/")
		ids = [t["id"] for t in resp.data["results"]]
		self.assertNotIn(self.priv_team.id, ids)

	def test_list_excludes_public_team_without_flag(self):
		resp = self.client.get("/teams/")
		ids = [t["id"] for t in resp.data["results"]]
		self.assertNotIn(self.pub_team.id, ids)

	def test_include_public_adds_public_teams(self):
		resp = self.client.get("/teams/?include_public=true")
		ids = [t["id"] for t in resp.data["results"]]
		self.assertIn(self.my_team.id, ids)
		self.assertIn(self.pub_team.id, ids)
		self.assertNotIn(self.priv_team.id, ids)

	def test_detail_hidden_returns_404(self):
		resp = self.client.get(f"/teams/{self.priv_team.id}/")
		self.assertEqual(resp.status_code, 404)

	def test_detail_own_returns_200(self):
		resp = self.client.get(f"/teams/{self.my_team.id}/")
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# API key caller (bound to my_org)
# ---------------------------------------------------------------------------


class APIKeyTeamVisibilityTest(TeamVisibilityBase):
	def setUp(self):
		super().setUp()
		self.scheme = _make_api_scheme(self.my_org, "team-key")
		self.client.credentials(HTTP_AUTHORIZATION=self.scheme.api_key)

	def test_list_shows_own_team(self):
		resp = self.client.get("/teams/")
		ids = [t["id"] for t in resp.data["results"]]
		self.assertIn(self.my_team.id, ids)

	def test_list_excludes_other_private_team(self):
		resp = self.client.get("/teams/")
		ids = [t["id"] for t in resp.data["results"]]
		self.assertNotIn(self.priv_team.id, ids)

	def test_list_excludes_public_team_without_flag(self):
		resp = self.client.get("/teams/")
		ids = [t["id"] for t in resp.data["results"]]
		self.assertNotIn(self.pub_team.id, ids)

	def test_include_public_adds_public_teams(self):
		resp = self.client.get("/teams/?include_public=true")
		ids = [t["id"] for t in resp.data["results"]]
		self.assertIn(self.my_team.id, ids)
		self.assertIn(self.pub_team.id, ids)
		self.assertNotIn(self.priv_team.id, ids)

	def test_detail_hidden_returns_404(self):
		resp = self.client.get(f"/teams/{self.priv_team.id}/")
		self.assertEqual(resp.status_code, 404)

	def test_detail_own_returns_200(self):
		resp = self.client.get(f"/teams/{self.my_team.id}/")
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Null-org API key (anonymous-equivalent)
# ---------------------------------------------------------------------------
