"""
Tests for the per-site editorial inline on the Articles admin page
(gregory.admin.ArticleSiteContentInline) and the read-only legacy
ArticleOrgContent admin.
"""

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.sites.models import Site
from django.test import TestCase
from django.urls import reverse
from organizations.models import Organization

from gregory.admin import ArticleOrgContentAdmin, ArticleSiteContentInline
from gregory.models import (
	ArticleOrgContent,
	Articles,
	ArticleSiteContent,
	OrganizationSite,
	Sources,
	Team,
)

User = get_user_model()


class ArticleSiteContentInlineTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Inline Org", slug="inline-org")
		self.other_org = Organization.objects.create(name="Inline Other", slug="inline-other")
		self.team = Team.objects.create(organization=self.org, name="Team", slug="inline-team")
		self.site = Site.objects.create(domain="inline.test.example.com", name="Inline")
		self.second_site = Site.objects.create(domain="inline2.test.example.com", name="Inline 2")
		self.foreign_site = Site.objects.create(domain="foreign.test.example.com", name="Foreign")
		OrganizationSite.objects.create(organization=self.org, site=self.site, is_default=True)
		OrganizationSite.objects.create(organization=self.org, site=self.second_site)
		OrganizationSite.objects.create(
			organization=self.other_org, site=self.foreign_site, is_default=True
		)
		self.article = Articles.objects.create(title="Inline article", link="https://i.test/1")
		self.article.teams.add(self.team)
		source = Sources.objects.create(name="Src", source_for="science paper", team=self.team)
		self.article.sources.add(source)
		ArticleSiteContent.objects.create(
			article=self.article, site=self.site, takeaways="First site"
		)
		ArticleSiteContent.objects.create(
			article=self.article, site=self.foreign_site, takeaways="Foreign site"
		)

		self.superuser = User.objects.create_superuser("inline-root", "r@example.com", "x")
		self.staff = User.objects.create_user(
			"inline-staff", "s@example.com", "x", is_staff=True
		)
		self.org.add_user(self.staff)
		self.staff.user_permissions.add(
			*Permission.objects.filter(
				content_type__app_label="gregory",
				content_type__model__in=["articles", "articlesitecontent"],
			)
		)
		self.url = reverse("admin:gregory_articles_change", args=[self.article.pk])

	def test_superuser_sees_every_sites_row(self):
		self.client.force_login(self.superuser)
		resp = self.client.get(self.url)
		self.assertEqual(resp.status_code, 200)
		self.assertContains(resp, "First site")
		self.assertContains(resp, "Foreign site")

	def test_staff_sees_only_their_organisations_sites(self):
		self.client.force_login(self.staff)
		resp = self.client.get(self.url)
		self.assertEqual(resp.status_code, 200)
		self.assertContains(resp, "First site")
		self.assertNotContains(resp, "Foreign site")

	def test_staff_site_choices_exclude_other_organisations_sites(self):
		request = type("R", (), {"user": self.staff})()
		sites = ArticleSiteContentInline._user_sites(request)
		self.assertEqual(set(sites), {self.site, self.second_site})

	def test_staff_can_only_add_rows_for_missing_own_sites(self):
		inline = ArticleSiteContentInline(Articles, admin.site)
		request = type("R", (), {"user": self.staff})()
		self.assertEqual(
			inline._missing_site_ids(request, self.article), [self.second_site.pk]
		)
		self.assertEqual(inline.get_max_num(request, self.article), 2)


class LegacyOrgContentAdminTest(TestCase):
	def test_article_org_content_admin_is_read_only(self):
		model_admin = ArticleOrgContentAdmin(ArticleOrgContent, admin.site)
		request = type("R", (), {"user": User(is_superuser=True, is_active=True, is_staff=True)})()
		self.assertFalse(model_admin.has_add_permission(request))
		self.assertFalse(model_admin.has_change_permission(request))
		self.assertFalse(model_admin.has_delete_permission(request))
