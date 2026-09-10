"""
Tests for gregory.admin.SiteScopeFilter — the "site" admin list filter that
narrows a changelist to one site the caller's organisation(s) own (Phase 4 PR
3, admin scoping).

A pure narrower: `.queryset()` runs on top of whatever OrganizationFilterMixin
already returned (see UnpublishedContentFilterTests' module docstring for why
that ordering is guaranteed), so picking a site can only remove rows from an
already org-scoped queryset -- never add another organisation's content, even
if the site id named belongs to another organisation entirely.
`test_selecting_a_foreign_site_id_cannot_widen_beyond_own_org` pins that down
directly. `lookups()` is tested separately because it is the other half of the
guarantee: it must not even disclose another organisation's site domains in
the dropdown.

Run with:
    docker exec gregory python manage.py test gregory.tests.test_admin_site_scope_filter
"""

from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import TestCase, RequestFactory
from organizations.models import Organization, OrganizationUser

from gregory.admin import OrganizationFilterMixin, SiteScopeFilter
from gregory.models import Articles, OrganizationSite, Sources, Subject, Team
from sitesettings.models import CustomSetting

User = get_user_model()


class _ArticleOrgAdmin(OrganizationFilterMixin, admin.ModelAdmin):
	"""Minimal admin: just enough to exercise get_queryset() + the filter."""


class SiteScopeFilterTests(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.admin_site = AdminSite()

		self.org_a = Organization.objects.create(name="Org A", slug="ssf-org-a")
		self.org_b = Organization.objects.create(name="Org B", slug="ssf-org-b")
		self.team_a = Team.objects.create(
			organization=self.org_a, name="Team A", slug="ssf-team-a"
		)
		self.team_b = Team.objects.create(
			organization=self.org_b, name="Team B", slug="ssf-team-b"
		)
		self.source_a = Sources.objects.create(
			name="Source A", source_for="science paper", team=self.team_a
		)
		self.source_b = Sources.objects.create(
			name="Source B", source_for="science paper", team=self.team_b
		)

		self.subject_a1 = Subject.objects.create(
			subject_name="A1", subject_slug="ssf-a1", team=self.team_a
		)
		self.subject_a2 = Subject.objects.create(
			subject_name="A2", subject_slug="ssf-a2", team=self.team_a
		)
		self.subject_b = Subject.objects.create(
			subject_name="B", subject_slug="ssf-b", team=self.team_b
		)

		self.django_site_a1 = Site.objects.create(
			domain="ssf-a1.test", name="Org A site 1"
		)
		self.setting_a1 = CustomSetting.objects.create(
			site=self.django_site_a1, title="Org A site 1 settings"
		)
		self.setting_a1.scope_subjects.add(self.subject_a1)
		OrganizationSite.objects.create(
			organization=self.org_a, site=self.django_site_a1, is_default=True
		)

		self.django_site_a2 = Site.objects.create(
			domain="ssf-a2.test", name="Org A site 2"
		)
		self.setting_a2 = CustomSetting.objects.create(
			site=self.django_site_a2, title="Org A site 2 settings"
		)
		self.setting_a2.scope_subjects.add(self.subject_a2)
		OrganizationSite.objects.create(organization=self.org_a, site=self.django_site_a2)

		self.django_site_b = Site.objects.create(
			domain="ssf-b.test", name="Org B site"
		)
		self.setting_b = CustomSetting.objects.create(
			site=self.django_site_b, title="Org B site settings"
		)
		self.setting_b.scope_subjects.add(self.subject_b)
		OrganizationSite.objects.create(
			organization=self.org_b, site=self.django_site_b, is_default=True
		)

		self.article_a1 = Articles.objects.create(
			title="A1", link="https://example.com/ssf-a1"
		)
		self.article_a1.sources.add(self.source_a)
		self.article_a1.subjects.add(self.subject_a1)

		self.article_a2 = Articles.objects.create(
			title="A2", link="https://example.com/ssf-a2"
		)
		self.article_a2.sources.add(self.source_a)
		self.article_a2.subjects.add(self.subject_a2)

		self.article_b = Articles.objects.create(
			title="B", link="https://example.com/ssf-b"
		)
		self.article_b.sources.add(self.source_b)
		self.article_b.subjects.add(self.subject_b)

		self.staff_a = User.objects.create_user(username="ssf-staff-a", password="pw")
		OrganizationUser.objects.create(organization=self.org_a, user=self.staff_a)

		self.superuser = User.objects.create_superuser(
			username="ssf-root", email="ssf-root@e.com", password="pw"
		)

	def _filter(self, request, value=None):
		params = {"site": [str(value)]} if value is not None else {}
		return SiteScopeFilter(
			request=request, params=params, model=Articles, model_admin=_ArticleOrgAdmin
		)

	def _request_for(self, user):
		request = self.factory.get("/admin/")
		request.user = user
		return request

	def _base_queryset(self, user):
		model_admin = _ArticleOrgAdmin(Articles, self.admin_site)
		return model_admin.get_queryset(self._request_for(user))

	def test_staff_lookups_lists_only_their_own_org_sites(self):
		request = self._request_for(self.staff_a)
		choices = self._filter(request).lookups(request, None)
		domains = [label for _, label in choices]
		self.assertIn(self.django_site_a1.domain, domains)
		self.assertIn(self.django_site_a2.domain, domains)
		# Info hygiene: org B's site domain must not even appear in the
		# dropdown offered to an org-A staff member.
		self.assertNotIn(self.django_site_b.domain, domains)

	def test_a_site_with_two_customsetting_rows_appears_once(self):
		"""CustomSetting.site is a plain FK, not unique.

		Two rows for one site would otherwise offer the same domain twice in
		the dropdown. Not hypothetical: exactly this shape produced duplicate
		rows in GET /sites/ (PR #859). No duplicate rows exist in production
		today, so without this test the guard would be unexercised.
		"""
		CustomSetting.objects.create(
			site=self.django_site_a1, title="Second settings row for A1"
		)
		request = self._request_for(self.staff_a)
		choices = self._filter(request).lookups(request, None)
		domains = [label for _, label in choices]
		self.assertEqual(domains.count(self.django_site_a1.domain), 1)
		site_ids = [value for value, _ in choices]
		self.assertEqual(len(site_ids), len(set(site_ids)))

	def test_superuser_lookups_lists_every_site(self):
		request = self._request_for(self.superuser)
		choices = self._filter(request).lookups(request, None)
		domains = [label for _, label in choices]
		self.assertIn(self.django_site_a1.domain, domains)
		self.assertIn(self.django_site_a2.domain, domains)
		self.assertIn(self.django_site_b.domain, domains)

	def test_no_value_returns_queryset_unchanged(self):
		request = self._request_for(self.staff_a)
		base_qs = self._base_queryset(self.staff_a)
		result = self._filter(request).queryset(request, base_qs)
		self.assertEqual(set(result), set(base_qs))

	def test_queryset_narrows_to_the_selected_site(self):
		request = self._request_for(self.staff_a)
		base_qs = self._base_queryset(self.staff_a)
		# Sanity: both of org A's own articles are visible before narrowing.
		self.assertIn(self.article_a1, base_qs)
		self.assertIn(self.article_a2, base_qs)

		result = self._filter(request, self.django_site_a1.pk).queryset(
			request, base_qs
		)
		self.assertIn(self.article_a1, result)
		self.assertNotIn(self.article_a2, result)

	def test_selecting_a_foreign_site_id_cannot_widen_beyond_own_org(self):
		"""Rule 4: a site filter narrows, it never widens. Even if a curious
		(or malicious) staff member crafts `?site=<org B's site id>` -- a
		value `lookups()` never offered them -- chaining onto their own
		org-scoped queryset means the result can only be a subset of their
		own organisation's content, never org B's."""
		request = self._request_for(self.staff_a)
		base_qs = self._base_queryset(self.staff_a)
		result = self._filter(request, self.django_site_b.pk).queryset(
			request, base_qs
		)
		self.assertNotIn(self.article_b, result)
		self.assertEqual(result.count(), 0)

	def test_superuser_can_select_any_site(self):
		request = self._request_for(self.superuser)
		base_qs = self._base_queryset(self.superuser)
		result = self._filter(request, self.django_site_b.pk).queryset(
			request, base_qs
		)
		self.assertIn(self.article_b, result)
		self.assertNotIn(self.article_a1, result)
