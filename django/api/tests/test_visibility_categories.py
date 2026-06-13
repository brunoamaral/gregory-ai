"""
Tests for category (TeamCategory) visibility enforcement (PR 5).

Covers:
  - CategoryViewSet list/detail: only categories whose team.org is visible
  - Detail endpoint 404s when category belongs to a hidden org
  - CategoriesByTeamAndSubject: hidden parent team → 404
  - Four caller archetypes × the standard test matrix

Run with:
    docker exec gregory python manage.py test api.tests.test_visibility_categories
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.timezone import now
from organizations.models import Organization, OrganizationUser
from rest_framework.test import APIClient

from api.models import APIAccessScheme
from gregory.models import OrganizationApiSettings, Subject, Team, TeamCategory

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


def _make_category(team, subject, name):
	from django.utils.text import slugify

	cat = TeamCategory.objects.create(
		team=team,
		category_name=name,
		category_slug=f"{team.slug}-{slugify(name)}",
	)
	cat.subjects.add(subject)
	return cat


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


class CategoryVisibilityBase(TestCase):
	def setUp(self):
		self.my_org = _make_org("My Org", "my-org-cat", public=False)
		self.pub_org = _make_org("Public Org", "pub-org-cat", public=True)
		self.priv_org = _make_org("Private Org", "priv-org-cat", public=False)

		self.my_team = _make_team(self.my_org, "My Team Cat")
		self.pub_team = _make_team(self.pub_org, "Pub Team Cat")
		self.priv_team = _make_team(self.priv_org, "Priv Team Cat")

		self.my_subj = _make_subject(self.my_team, "My Subj Cat")
		self.pub_subj = _make_subject(self.pub_team, "Pub Subj Cat")
		self.priv_subj = _make_subject(self.priv_team, "Priv Subj Cat")

		self.cat_mine = _make_category(self.my_team, self.my_subj, "Mine Category")
		self.cat_pub = _make_category(self.pub_team, self.pub_subj, "Public Category")
		self.cat_priv = _make_category(
			self.priv_team, self.priv_subj, "Private Category"
		)

		self.client = APIClient()


# ---------------------------------------------------------------------------
# Anonymous caller
# ---------------------------------------------------------------------------


class AnonymousCategoryVisibilityTest(CategoryVisibilityBase):
	def test_list_includes_public_category(self):
		resp = self.client.get("/categories/")
		self.assertEqual(resp.status_code, 200)
		ids = [c["id"] for c in resp.data["results"]]
		self.assertIn(self.cat_pub.id, ids)

	def test_list_excludes_private_categories(self):
		resp = self.client.get("/categories/")
		ids = [c["id"] for c in resp.data["results"]]
		self.assertNotIn(self.cat_mine.id, ids)
		self.assertNotIn(self.cat_priv.id, ids)

	def test_detail_hidden_returns_404(self):
		resp = self.client.get(f"/categories/{self.cat_mine.id}/")
		self.assertEqual(resp.status_code, 404)

	def test_detail_public_returns_200(self):
		resp = self.client.get(f"/categories/{self.cat_pub.id}/")
		self.assertEqual(resp.status_code, 200)

	def test_categories_by_team_hidden_team_returns_404(self):
		resp = self.client.get(
			f"/teams/{self.my_team.id}/subjects/{self.my_subj.id}/categories/"
		)
		self.assertEqual(resp.status_code, 404)

	def test_categories_by_team_public_team_returns_200(self):
		resp = self.client.get(
			f"/teams/{self.pub_team.id}/subjects/{self.pub_subj.id}/categories/"
		)
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Authenticated user (member of my_org)
# ---------------------------------------------------------------------------


class AuthenticatedUserCategoryVisibilityTest(CategoryVisibilityBase):
	def setUp(self):
		super().setUp()
		self.user = User.objects.create_user(username="cat-member", password="pw")
		OrganizationUser.objects.create(organization=self.my_org, user=self.user)
		self.client.force_login(self.user)

	def test_list_shows_own_org_category(self):
		resp = self.client.get("/categories/")
		ids = [c["id"] for c in resp.data["results"]]
		self.assertIn(self.cat_mine.id, ids)

	def test_list_excludes_unrelated_private_category(self):
		resp = self.client.get("/categories/")
		ids = [c["id"] for c in resp.data["results"]]
		self.assertNotIn(self.cat_priv.id, ids)

	def test_list_excludes_public_category_without_flag(self):
		resp = self.client.get("/categories/")
		ids = [c["id"] for c in resp.data["results"]]
		self.assertNotIn(self.cat_pub.id, ids)

	def test_include_public_adds_public_categories(self):
		resp = self.client.get("/categories/?include_public=true")
		ids = [c["id"] for c in resp.data["results"]]
		self.assertIn(self.cat_mine.id, ids)
		self.assertIn(self.cat_pub.id, ids)
		self.assertNotIn(self.cat_priv.id, ids)

	def test_detail_hidden_returns_404(self):
		resp = self.client.get(f"/categories/{self.cat_priv.id}/")
		self.assertEqual(resp.status_code, 404)

	def test_detail_own_returns_200(self):
		resp = self.client.get(f"/categories/{self.cat_mine.id}/")
		self.assertEqual(resp.status_code, 200)

	def test_categories_by_team_own_team_returns_200(self):
		resp = self.client.get(
			f"/teams/{self.my_team.id}/subjects/{self.my_subj.id}/categories/"
		)
		self.assertEqual(resp.status_code, 200)

	def test_categories_by_team_hidden_team_returns_404(self):
		resp = self.client.get(
			f"/teams/{self.priv_team.id}/subjects/{self.priv_subj.id}/categories/"
		)
		self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# API key caller (bound to my_org)
# ---------------------------------------------------------------------------


class APIKeyCategoryVisibilityTest(CategoryVisibilityBase):
	def setUp(self):
		super().setUp()
		self.scheme = _make_api_scheme(self.my_org, "cat-key")
		self.client.credentials(HTTP_AUTHORIZATION=self.scheme.api_key)

	def test_list_shows_own_category(self):
		resp = self.client.get("/categories/")
		ids = [c["id"] for c in resp.data["results"]]
		self.assertIn(self.cat_mine.id, ids)

	def test_list_excludes_private_category(self):
		resp = self.client.get("/categories/")
		ids = [c["id"] for c in resp.data["results"]]
		self.assertNotIn(self.cat_priv.id, ids)

	def test_include_public_adds_public_categories(self):
		resp = self.client.get("/categories/?include_public=true")
		ids = [c["id"] for c in resp.data["results"]]
		self.assertIn(self.cat_mine.id, ids)
		self.assertIn(self.cat_pub.id, ids)
		self.assertNotIn(self.cat_priv.id, ids)

	def test_detail_hidden_returns_404(self):
		resp = self.client.get(f"/categories/{self.cat_priv.id}/")
		self.assertEqual(resp.status_code, 404)

	def test_detail_own_returns_200(self):
		resp = self.client.get(f"/categories/{self.cat_mine.id}/")
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Null-org API key (anonymous-equivalent)
# ---------------------------------------------------------------------------
