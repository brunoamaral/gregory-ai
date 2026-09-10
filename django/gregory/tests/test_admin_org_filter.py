"""
Tests for gregory.admin.OrganizationFilterMixin.get_queryset — the Django admin
queryset scoping that limits non-superusers to their own organisation's objects.

Guards the fix where a bare `except: pass` could silently return the UNFILTERED
queryset (an org-visibility leak) when the team-scoping branch raised.

Content (Articles, Trials) is scoped through source -> team -> organisation,
deliberately NOT through the model's own `teams` M2M -- see
OrganizationFilterMixin's docstring in gregory/admin.py for the rationale.
These tests attach `sources`, not `teams`, to pin that down, and
test_article_scoped_via_its_sources_team_not_its_own_teams_field asserts the
divergence directly.

Run with:
    docker exec gregory python manage.py test gregory.tests.test_admin_org_filter
"""

from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import TestCase, RequestFactory
from organizations.models import Organization, OrganizationUser

from gregory.admin import OrganizationFilterMixin
from gregory.models import Articles, Authors, Sources, Team, Trials

User = get_user_model()


class _ArticleOrgAdmin(OrganizationFilterMixin, admin.ModelAdmin):
	"""Minimal admin exercising the mixin on a model with a `sources` M2M."""


class _TrialOrgAdmin(OrganizationFilterMixin, admin.ModelAdmin):
	"""Minimal admin exercising the mixin on Trials, the other content model."""


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
		self.source_a = Sources.objects.create(
			name="Source A", source_for="science paper", team=self.team_a
		)
		self.source_b = Sources.objects.create(
			name="Source B", source_for="science paper", team=self.team_b
		)
		self.trial_source_a = Sources.objects.create(
			name="Trial Source A", source_for="trials", team=self.team_a
		)
		self.trial_source_b = Sources.objects.create(
			name="Trial Source B", source_for="trials", team=self.team_b
		)

		self.article_a = Articles.objects.create(
			title="Article A", link="https://example.com/a"
		)
		self.article_a.sources.add(self.source_a)
		self.article_b = Articles.objects.create(
			title="Article B", link="https://example.com/b"
		)
		self.article_b.sources.add(self.source_b)
		# No source at all: attributable to no organisation (spec rule 5).
		self.article_sourceless = Articles.objects.create(
			title="Article sourceless", link="https://example.com/sourceless"
		)

		self.trial_a = Trials.objects.create(
			title="Trial A", link="https://example.com/trial-a"
		)
		self.trial_a.sources.add(self.trial_source_a)
		self.trial_b = Trials.objects.create(
			title="Trial B", link="https://example.com/trial-b"
		)
		self.trial_b.sources.add(self.trial_source_b)

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

	def test_non_superuser_sees_only_their_org_trials(self):
		# Same rule, other content model: Trials -> sources -> team -> org.
		qs = self._queryset_for(_TrialOrgAdmin, Trials, self.user)
		self.assertIn(self.trial_a, qs)
		self.assertNotIn(self.trial_b, qs)

	def test_superuser_sees_sourceless_article(self):
		qs = self._queryset_for(_ArticleOrgAdmin, Articles, self.superuser)
		self.assertIn(self.article_sourceless, qs)

	def test_non_superuser_never_sees_sourceless_article(self):
		# Spec rule 5: content with no source at all resolves to no
		# organisation, so it is superuser-only. Not a special case in the
		# mixin -- just what the source->team->org filter naturally excludes,
		# for every non-superuser regardless of which org they belong to.
		qs = self._queryset_for(_ArticleOrgAdmin, Articles, self.user)
		self.assertNotIn(self.article_sourceless, qs)

	def test_article_scoped_via_its_sources_team_not_its_own_teams_field(self):
		# An article curated onto org A's team via the `teams` M2M, but whose
		# only source belongs to org B, must be scoped by the SOURCE's team --
		# not by the article's own (curator-assigned, sometimes-absent)
		# `teams` field. Fails under the pre-change `teams`-based scoping,
		# which would have shown this to the org-A user.
		mismatched = Articles.objects.create(
			title="Mismatched", link="https://example.com/mismatched"
		)
		mismatched.sources.add(self.source_b)
		mismatched.teams.add(self.team_a)
		qs = self._queryset_for(_ArticleOrgAdmin, Articles, self.user)
		self.assertNotIn(mismatched, qs)

	def test_model_without_teams_falls_through_unscoped(self):
		# Authors has no organisation/team/sources: the FieldDoesNotExist
		# branch returns the queryset unchanged (these models aren't
		# org-scoped).
		author = Authors.objects.create(given_name="Jane", family_name="Doe")
		qs = self._queryset_for(_AuthorOrgAdmin, Authors, self.user)
		self.assertIn(author, qs)
