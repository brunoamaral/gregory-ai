"""
Tests for gregory.admin.OrganizationFilterMixin.get_queryset — the Django admin
queryset scoping that limits non-superusers to their own organisation's objects.

Guards the fix where a bare `except: pass` could silently return the UNFILTERED
queryset (an org-visibility leak) when the team-scoping branch raised.

Run with:
    docker exec gregory python manage.py test gregory.tests.test_admin_org_filter
"""

from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import TestCase, RequestFactory
from organizations.models import Organization, OrganizationUser

from gregory.admin import OrganizationFilterMixin
from gregory.models import Articles, Authors, Team

User = get_user_model()


class _ArticleOrgAdmin(OrganizationFilterMixin, admin.ModelAdmin):
	"""Minimal admin exercising the mixin on a model with a `teams` M2M."""


class _AuthorOrgAdmin(OrganizationFilterMixin, admin.ModelAdmin):
	"""Minimal admin on a model with no team/org relationship."""


class OrganizationFilterMixinTests(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.site = AdminSite()

		self.org_a = Organization.objects.create(name="Org A", slug="org-a-admin")
		self.org_b = Organization.objects.create(name="Org B", slug="org-b-admin")
		self.team_a = Team.objects.create(
			organization=self.org_a, name="Team A", slug="team-a-admin"
		)
		self.team_b = Team.objects.create(
			organization=self.org_b, name="Team B", slug="team-b-admin"
		)

		self.article_a = Articles.objects.create(
			title="Article A", link="https://example.com/a"
		)
		self.article_a.teams.add(self.team_a)
		self.article_b = Articles.objects.create(
			title="Article B", link="https://example.com/b"
		)
		self.article_b.teams.add(self.team_b)

		# Non-superuser belonging only to org A.
		self.user = User.objects.create_user(username="org-a-user", password="pw")
		OrganizationUser.objects.create(organization=self.org_a, user=self.user)

		self.superuser = User.objects.create_superuser(
			username="root", email="r@e.com", password="pw"
		)

	def _queryset_for(self, admin_cls, model, user):
		model_admin = admin_cls(model, self.site)
		request = self.factory.get("/admin/")
		request.user = user
		return model_admin.get_queryset(request)

	def test_superuser_sees_all_articles(self):
		qs = self._queryset_for(_ArticleOrgAdmin, Articles, self.superuser)
		self.assertIn(self.article_a, qs)
		self.assertIn(self.article_b, qs)

	def test_non_superuser_sees_only_their_org(self):
		# Regression guard: an org-A user must NOT see org-B's article. If the
		# mixin ever falls back to the unfiltered queryset, this fails.
		qs = self._queryset_for(_ArticleOrgAdmin, Articles, self.user)
		self.assertIn(self.article_a, qs)
		self.assertNotIn(self.article_b, qs)

	def test_model_without_teams_falls_through_unscoped(self):
		# Authors has no organisation/team/teams: the FieldDoesNotExist branch
		# returns the queryset unchanged (these models aren't org-scoped).
		author = Authors.objects.create(given_name="Jane", family_name="Doe")
		qs = self._queryset_for(_AuthorOrgAdmin, Authors, self.user)
		self.assertIn(author, qs)
