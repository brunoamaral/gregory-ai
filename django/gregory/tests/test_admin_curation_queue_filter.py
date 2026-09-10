"""
Tests for gregory.admin.UnpublishedContentFilter — the "not in any site's
scope" admin list filter (Phase 4 PR 3, admin scoping).

This is the curation queue: content none of whose subjects has been added to
any site's `scope_subjects` yet. The filter's `.queryset()` runs on whatever
queryset Django's ChangeList hands it, which is always the ModelAdmin's OWN
`get_queryset(request)` result first (see ChangeList.get_queryset in
django/contrib/admin/views/main.py) -- list filters chain on top of that, they
never see the raw table. That ordering is load-bearing here: it is what scopes
the queue to the caller's OWN organisation for free, per
OrganizationFilterMixin's source -> team -> organisation scoping. A version of
this filter that queried Articles/Trials directly instead of filtering the
queryset it was given would leak every organisation's uncurated content to
every staff member -- test_staff_curation_queue_has_no_cross_org_leak pins
that down explicitly.

Run with:
    docker exec gregory python manage.py test gregory.tests.test_admin_curation_queue_filter
"""

from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import TestCase, RequestFactory
from organizations.models import Organization, OrganizationUser

from gregory.admin import OrganizationFilterMixin, UnpublishedContentFilter
from gregory.models import Articles, Sources, Subject, Team
from sitesettings.models import CustomSetting

User = get_user_model()


class _ArticleOrgAdmin(OrganizationFilterMixin, admin.ModelAdmin):
	"""Minimal admin: just enough to exercise get_queryset() + the filter."""


class UnpublishedContentFilterTests(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.site = AdminSite()

		self.org_a = Organization.objects.create(name="Org A", slug="uq-org-a")
		self.org_b = Organization.objects.create(name="Org B", slug="uq-org-b")
		self.team_a = Team.objects.create(
			organization=self.org_a, name="Team A", slug="uq-team-a"
		)
		self.team_b = Team.objects.create(
			organization=self.org_b, name="Team B", slug="uq-team-b"
		)
		self.source_a = Sources.objects.create(
			name="Source A", source_for="science paper", team=self.team_a
		)
		self.source_b = Sources.objects.create(
			name="Source B", source_for="science paper", team=self.team_b
		)

		# subject_a_published sits in a site's scope; subject_a_unpublished and
		# subject_b sit in none.
		self.subject_a_published = Subject.objects.create(
			subject_name="A Published", subject_slug="uq-a-pub", team=self.team_a
		)
		self.subject_a_unpublished = Subject.objects.create(
			subject_name="A Unpublished", subject_slug="uq-a-unpub", team=self.team_a
		)
		self.subject_b = Subject.objects.create(
			subject_name="B", subject_slug="uq-b", team=self.team_b
		)

		django_site = Site.objects.create(domain="uq-site.test", name="UQ site")
		self.custom_setting = CustomSetting.objects.create(
			site=django_site, title="UQ site settings"
		)
		self.custom_setting.scope_subjects.add(self.subject_a_published)

		self.article_a_published = Articles.objects.create(
			title="A published", link="https://example.com/uq-a-pub"
		)
		self.article_a_published.sources.add(self.source_a)
		self.article_a_published.subjects.add(self.subject_a_published)

		self.article_a_unpublished = Articles.objects.create(
			title="A unpublished", link="https://example.com/uq-a-unpub"
		)
		self.article_a_unpublished.sources.add(self.source_a)
		self.article_a_unpublished.subjects.add(self.subject_a_unpublished)

		self.article_a_no_subjects = Articles.objects.create(
			title="A no subjects", link="https://example.com/uq-a-none"
		)
		self.article_a_no_subjects.sources.add(self.source_a)

		self.article_b_unpublished = Articles.objects.create(
			title="B unpublished", link="https://example.com/uq-b-unpub"
		)
		self.article_b_unpublished.sources.add(self.source_b)
		self.article_b_unpublished.subjects.add(self.subject_b)

		self.staff_a = User.objects.create_user(username="uq-staff-a", password="pw")
		OrganizationUser.objects.create(organization=self.org_a, user=self.staff_a)

		self.superuser = User.objects.create_superuser(
			username="uq-root", email="uq-root@e.com", password="pw"
		)

	def _filter(self, value=None):
		params = {"unpublished": [value]} if value is not None else {}
		return UnpublishedContentFilter(
			request=None, params=params, model=Articles, model_admin=_ArticleOrgAdmin
		)

	def _base_queryset(self, user):
		model_admin = _ArticleOrgAdmin(Articles, self.site)
		request = self.factory.get("/admin/")
		request.user = user
		return model_admin.get_queryset(request)

	def test_lookups_is_a_single_opt_in_choice(self):
		self.assertEqual(
			self._filter().lookups(None, None),
			(("1", "Not in any site's scope (curation queue)"),),
		)

	def test_no_value_returns_queryset_unchanged(self):
		result = self._filter().queryset(None, Articles.objects.all())
		self.assertEqual(result.count(), Articles.objects.count())

	def test_excludes_content_with_a_scoped_subject(self):
		result = self._filter("1").queryset(None, Articles.objects.all())
		self.assertNotIn(self.article_a_published, result)

	def test_includes_content_with_an_unscoped_subject(self):
		result = self._filter("1").queryset(None, Articles.objects.all())
		self.assertIn(self.article_a_unpublished, result)

	def test_includes_content_with_no_subjects_at_all(self):
		# Vacuously true: "none of its subjects is scoped" holds when there
		# are no subjects to check.
		result = self._filter("1").queryset(None, Articles.objects.all())
		self.assertIn(self.article_a_no_subjects, result)

	def test_staff_curation_queue_has_no_cross_org_leak(self):
		"""The leak that matters most: stated as a bare "not in any site's
		scope" query, org B's uncurated article would show up for an org-A
		staff member too. Chaining onto get_queryset()'s result -- exactly
		what Django's ChangeList does -- must prevent that."""
		base_qs = self._base_queryset(self.staff_a)
		result = self._filter("1").queryset(None, base_qs)
		self.assertIn(self.article_a_unpublished, result)
		self.assertIn(self.article_a_no_subjects, result)
		self.assertNotIn(self.article_b_unpublished, result)
		self.assertNotIn(self.article_a_published, result)

	def test_superuser_sees_the_global_curation_queue(self):
		base_qs = self._base_queryset(self.superuser)
		result = self._filter("1").queryset(None, base_qs)
		self.assertIn(self.article_a_unpublished, result)
		self.assertIn(self.article_b_unpublished, result)
		self.assertNotIn(self.article_a_published, result)
