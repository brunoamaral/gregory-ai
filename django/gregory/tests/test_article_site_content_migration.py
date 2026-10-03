"""
Data migration 0105: ArticleOrgContent rows are copied to every site the
organisation owns, so each site keeps showing what it showed before editorial
content became per site.

pytest runs with --nomigrations, so the migration's function is called
directly against the test schema.
"""

import importlib

from django.apps import apps
from django.contrib.sites.models import Site
from django.db import connection
from django.test import TestCase
from organizations.models import Organization

from gregory.models import (
	ArticleOrgContent,
	Articles,
	ArticleSiteContent,
	OrganizationSite,
)

migration = importlib.import_module(
	"gregory.migrations.0105_copy_org_content_to_site_content"
)


def _run_copy():
	with connection.schema_editor() as schema_editor:
		migration.copy_org_content_to_sites(apps, schema_editor)


class CopyOrgContentToSitesTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Two Sites Org", slug="two-sites-org")
		self.site_one = Site.objects.create(domain="one.migration.example.com", name="One")
		self.site_two = Site.objects.create(domain="two.migration.example.com", name="Two")
		OrganizationSite.objects.create(
			organization=self.org, site=self.site_one, is_default=True
		)
		OrganizationSite.objects.create(organization=self.org, site=self.site_two)
		self.article = Articles.objects.create(title="Migrated", link="https://m.test/1")

	def test_content_is_copied_to_every_site_of_the_organisation(self):
		ArticleOrgContent.objects.create(
			article=self.article,
			organization=self.org,
			takeaways="Org takeaway",
			summary_plain_english="Org plain",
		)

		_run_copy()

		rows = {r.site_id: r for r in ArticleSiteContent.objects.filter(article=self.article)}
		self.assertEqual(set(rows), {self.site_one.pk, self.site_two.pk})
		for row in rows.values():
			self.assertEqual(row.takeaways, "Org takeaway")
			self.assertEqual(row.summary_plain_english, "Org plain")

	def test_other_organisations_content_does_not_leak_across(self):
		other_org = Organization.objects.create(name="Other Org", slug="other-org")
		other_site = Site.objects.create(domain="other.migration.example.com", name="O")
		OrganizationSite.objects.create(organization=other_org, site=other_site)
		ArticleOrgContent.objects.create(
			article=self.article, organization=other_org, takeaways="Other org"
		)

		_run_copy()

		self.assertEqual(
			list(ArticleSiteContent.objects.values_list("site_id", "takeaways")),
			[(other_site.pk, "Other org")],
		)

	def test_timestamps_and_nulls_are_preserved(self):
		row = ArticleOrgContent.objects.create(
			article=self.article, organization=self.org, takeaways=None
		)

		_run_copy()

		copied = ArticleSiteContent.objects.get(article=self.article, site=self.site_one)
		self.assertIsNone(copied.takeaways)
		self.assertEqual(copied.created_at, row.created_at)
		self.assertEqual(copied.updated_at, row.updated_at)

	def test_organisation_without_a_site_is_skipped(self):
		bare = Organization.objects.create(name="Bare Org", slug="bare-org")
		ArticleOrgContent.objects.create(
			article=self.article, organization=bare, takeaways="No site"
		)

		_run_copy()

		self.assertEqual(ArticleSiteContent.objects.count(), 0)

	def test_rerun_does_not_overwrite_or_duplicate(self):
		ArticleOrgContent.objects.create(
			article=self.article, organization=self.org, takeaways="Original"
		)
		_run_copy()
		ArticleSiteContent.objects.filter(site=self.site_one).update(takeaways="Edited")

		_run_copy()

		self.assertEqual(ArticleSiteContent.objects.count(), 2)
		self.assertEqual(
			ArticleSiteContent.objects.get(site=self.site_one).takeaways, "Edited"
		)

	def test_empty_database_is_a_noop(self):
		_run_copy()

		self.assertEqual(ArticleSiteContent.objects.count(), 0)
