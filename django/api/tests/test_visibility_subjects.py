"""
Tests for subject visibility enforcement (PR 5).

Covers:
  - SubjectsViewSet list/detail: only subjects whose team.organization is visible
  - Detail endpoint 404s when subject belongs to a hidden org
  - Four caller archetypes × the standard test matrix

Run with:
    docker exec gregory python manage.py test api.tests.test_visibility_subjects
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.timezone import now
from organizations.models import Organization, OrganizationUser
from rest_framework.test import APIClient

from api.models import APIAccessScheme
from gregory.models import OrganizationApiSettings, Subject, Team

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


class SubjectVisibilityBase(TestCase):
	def setUp(self):
		self.my_org = _make_org("My Org", "my-org-subj", public=False)
		self.pub_org = _make_org("Public Org", "pub-org-subj", public=True)
		self.priv_org = _make_org("Private Org", "priv-org-subj", public=False)

		self.my_team = _make_team(self.my_org, "My Team Subj")
		self.pub_team = _make_team(self.pub_org, "Pub Team Subj")
		self.priv_team = _make_team(self.priv_org, "Priv Team Subj")

		self.subj_mine = _make_subject(self.my_team, "Mine Subj")
		self.subj_pub = _make_subject(self.pub_team, "Public Subj")
		self.subj_priv = _make_subject(self.priv_team, "Private Subj")

		self.client = APIClient()


# ---------------------------------------------------------------------------
# Anonymous caller
# ---------------------------------------------------------------------------


class AnonymousSubjectVisibilityTest(SubjectVisibilityBase):
	def test_list_includes_public_subject(self):
		resp = self.client.get("/subjects/")
		self.assertEqual(resp.status_code, 200)
		ids = [s["id"] for s in resp.data["results"]]
		self.assertIn(self.subj_pub.id, ids)

	def test_list_excludes_private_subjects(self):
		resp = self.client.get("/subjects/")
		ids = [s["id"] for s in resp.data["results"]]
		self.assertNotIn(self.subj_mine.id, ids)
		self.assertNotIn(self.subj_priv.id, ids)

	def test_detail_hidden_returns_404(self):
		resp = self.client.get(f"/subjects/{self.subj_mine.id}/")
		self.assertEqual(resp.status_code, 404)

	def test_detail_public_returns_200(self):
		resp = self.client.get(f"/subjects/{self.subj_pub.id}/")
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Authenticated user (member of my_org)
# ---------------------------------------------------------------------------


class AuthenticatedUserSubjectVisibilityTest(SubjectVisibilityBase):
	def setUp(self):
		super().setUp()
		self.user = User.objects.create_user(username="subj-member", password="pw")
		OrganizationUser.objects.create(organization=self.my_org, user=self.user)
		self.client.force_login(self.user)

	def test_list_shows_own_org_subject(self):
		resp = self.client.get("/subjects/")
		ids = [s["id"] for s in resp.data["results"]]
		self.assertIn(self.subj_mine.id, ids)

	def test_list_excludes_unrelated_private_subject(self):
		resp = self.client.get("/subjects/")
		ids = [s["id"] for s in resp.data["results"]]
		self.assertNotIn(self.subj_priv.id, ids)

	def test_list_excludes_public_subject_without_flag(self):
		resp = self.client.get("/subjects/")
		ids = [s["id"] for s in resp.data["results"]]
		self.assertNotIn(self.subj_pub.id, ids)

	def test_include_public_adds_public_subjects(self):
		resp = self.client.get("/subjects/?include_public=true")
		ids = [s["id"] for s in resp.data["results"]]
		self.assertIn(self.subj_mine.id, ids)
		self.assertIn(self.subj_pub.id, ids)
		self.assertNotIn(self.subj_priv.id, ids)

	def test_detail_hidden_returns_404(self):
		resp = self.client.get(f"/subjects/{self.subj_priv.id}/")
		self.assertEqual(resp.status_code, 404)

	def test_detail_own_returns_200(self):
		resp = self.client.get(f"/subjects/{self.subj_mine.id}/")
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# API key caller (bound to my_org)
# ---------------------------------------------------------------------------


class APIKeySubjectVisibilityTest(SubjectVisibilityBase):
	def setUp(self):
		super().setUp()
		self.scheme = _make_api_scheme(self.my_org, "subj-key")
		self.client.credentials(HTTP_AUTHORIZATION=self.scheme.api_key)

	def test_list_shows_own_subject(self):
		resp = self.client.get("/subjects/")
		ids = [s["id"] for s in resp.data["results"]]
		self.assertIn(self.subj_mine.id, ids)

	def test_list_excludes_private_subject(self):
		resp = self.client.get("/subjects/")
		ids = [s["id"] for s in resp.data["results"]]
		self.assertNotIn(self.subj_priv.id, ids)

	def test_list_excludes_public_subject_without_flag(self):
		resp = self.client.get("/subjects/")
		ids = [s["id"] for s in resp.data["results"]]
		self.assertNotIn(self.subj_pub.id, ids)

	def test_include_public_adds_public_subjects(self):
		resp = self.client.get("/subjects/?include_public=true")
		ids = [s["id"] for s in resp.data["results"]]
		self.assertIn(self.subj_mine.id, ids)
		self.assertIn(self.subj_pub.id, ids)
		self.assertNotIn(self.subj_priv.id, ids)

	def test_detail_hidden_returns_404(self):
		resp = self.client.get(f"/subjects/{self.subj_priv.id}/")
		self.assertEqual(resp.status_code, 404)

	def test_detail_own_returns_200(self):
		resp = self.client.get(f"/subjects/{self.subj_mine.id}/")
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Null-org API key (anonymous-equivalent)
# ---------------------------------------------------------------------------
