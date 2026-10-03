import os
from unittest.mock import patch, MagicMock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")

import django

django.setup()

from django.test import TestCase
from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib.sites.models import Site
from organizations.models import Organization
from gregory.models import Articles, ArticleSiteContent, OrganizationSite


def _make_response(results, next_url=None):
	mock = MagicMock()
	mock.raise_for_status.return_value = None
	mock.json.return_value = {"results": results, "next": next_url}
	return mock


ARTICLE = {
	"title": "Test Article",
	"link": "https://example.com/article/1",
	"doi": "10.1234/test",
	"summary": "A test abstract.",
	"published_date": "2024-01-15T00:00:00Z",
	"publisher": "Test Publisher",
	"container_title": "Test Journal",
	"access": "open",
	"takeaways": "Key finding A.",
	"summary_plain_english": "Simple explanation.",
	"discovery_date": "2024-01-16T00:00:00Z",
	"authors": [],
	"sources": [],
	"teams": [],
	"subjects": [],
	"article_subject_relevances": [],
}


class ImportArticlesFromApiTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Test Org", slug="test-org")
		self.site = Site.objects.create(domain="import.test.example.com", name="Import")
		OrganizationSite.objects.create(
			organization=self.org, site=self.site, is_default=True
		)

	@patch("gregory.management.commands.import_articles_from_api.requests.get")
	def test_import_populates_article_site_content(self, mock_get):
		mock_get.return_value = _make_response([ARTICLE])

		call_command(
			"import_articles_from_api",
			"https://api.example.com/articles/",
			"--target-org",
			"test-org",
		)

		article = Articles.objects.get(title="Test Article")
		content = ArticleSiteContent.objects.get(article=article, site=self.site)
		self.assertEqual(content.takeaways, "Key finding A.")
		self.assertEqual(content.summary_plain_english, "Simple explanation.")

	@patch("gregory.management.commands.import_articles_from_api.requests.get")
	def test_reimport_updates_existing_site_content(self, mock_get):
		mock_get.return_value = _make_response([ARTICLE])
		call_command(
			"import_articles_from_api",
			"https://api.example.com/articles/",
			"--target-org",
			"test-org",
		)

		updated = {
			**ARTICLE,
			"takeaways": "Updated takeaway.",
			"summary_plain_english": "Updated summary.",
		}
		mock_get.return_value = _make_response([updated])
		call_command(
			"import_articles_from_api",
			"https://api.example.com/articles/",
			"--target-org",
			"test-org",
		)

		article = Articles.objects.get(title="Test Article")
		content = ArticleSiteContent.objects.get(article=article, site=self.site)
		self.assertEqual(content.takeaways, "Updated takeaway.")
		self.assertEqual(content.summary_plain_english, "Updated summary.")
		self.assertEqual(
			ArticleSiteContent.objects.filter(
				article=article, site=self.site
			).count(),
			1,
		)

	@patch("gregory.management.commands.import_articles_from_api.requests.get")
	def test_none_takeaways_leaves_existing_row_untouched(self, mock_get):
		"""Absent / null fields must not overwrite existing ArticleSiteContent values."""
		article = Articles.objects.create(
			title="Test Article", link="https://example.com/article/1"
		)
		ArticleSiteContent.objects.create(
			article=article,
			site=self.site,
			takeaways="Original takeaway.",
			summary_plain_english="Original summary.",
		)

		null_fields = {**ARTICLE, "takeaways": None, "summary_plain_english": None}
		mock_get.return_value = _make_response([null_fields])
		call_command(
			"import_articles_from_api",
			"https://api.example.com/articles/",
			"--target-org",
			"test-org",
		)

		content = ArticleSiteContent.objects.get(article=article, site=self.site)
		self.assertEqual(content.takeaways, "Original takeaway.")
		self.assertEqual(content.summary_plain_english, "Original summary.")

	@patch("gregory.management.commands.import_articles_from_api.requests.get")
	def test_empty_string_takeaways_clears_existing_row(self, mock_get):
		"""Upstream sending '' should normalize to None and clear the stored value."""
		article = Articles.objects.create(
			title="Test Article", link="https://example.com/article/1"
		)
		ArticleSiteContent.objects.create(
			article=article,
			site=self.site,
			takeaways="Stale takeaway.",
			summary_plain_english="Stale summary.",
		)

		empty_strings = {**ARTICLE, "takeaways": "", "summary_plain_english": ""}
		mock_get.return_value = _make_response([empty_strings])
		call_command(
			"import_articles_from_api",
			"https://api.example.com/articles/",
			"--target-org",
			"test-org",
		)

		content = ArticleSiteContent.objects.get(article=article, site=self.site)
		self.assertIsNone(content.takeaways)
		self.assertIsNone(content.summary_plain_english)

	def test_missing_target_org_raises_command_error(self):
		with self.assertRaises((CommandError, SystemExit)):
			call_command(
				"import_articles_from_api", "https://api.example.com/articles/"
			)

	@patch("gregory.management.commands.import_articles_from_api.requests.get")
	def test_unknown_org_raises_command_error(self, mock_get):
		mock_get.return_value = _make_response([ARTICLE])
		with self.assertRaises(CommandError):
			call_command(
				"import_articles_from_api",
				"https://api.example.com/articles/",
				"--target-org",
				"nonexistent-org",
			)

	@patch("gregory.management.commands.import_articles_from_api.requests.get")
	def test_every_site_of_the_org_receives_the_content(self, mock_get):
		mock_get.return_value = _make_response([ARTICLE])
		second = Site.objects.create(domain="import2.test.example.com", name="Import 2")
		OrganizationSite.objects.create(organization=self.org, site=second)

		call_command(
			"import_articles_from_api",
			"https://api.example.com/articles/",
			"--target-org",
			"test-org",
		)

		article = Articles.objects.get(title="Test Article")
		self.assertEqual(
			set(ArticleSiteContent.objects.filter(article=article).values_list("site_id", flat=True)),
			{self.site.pk, second.pk},
		)

	@patch("gregory.management.commands.import_articles_from_api.requests.get")
	def test_nested_editorial_entry_is_imported(self, mock_get):
		item = {
			**ARTICLE,
			"takeaways": None,
			"summary_plain_english": None,
			"editorial": [
				{
					"site": {"id": 1, "domain": "x.example.com", "name": "X"},
					"takeaways": "From the nested entry.",
					"summary_plain_english": None,
				}
			],
		}
		mock_get.return_value = _make_response([item])

		call_command(
			"import_articles_from_api",
			"https://api.example.com/articles/",
			"--target-org",
			"test-org",
		)

		article = Articles.objects.get(title="Test Article")
		content = ArticleSiteContent.objects.get(article=article, site=self.site)
		self.assertEqual(content.takeaways, "From the nested entry.")

	@patch("gregory.management.commands.import_articles_from_api.requests.get")
	def test_org_without_a_site_raises_command_error(self, mock_get):
		mock_get.return_value = _make_response([ARTICLE])
		Organization.objects.create(name="No Site Org", slug="no-site-org")
		with self.assertRaises(CommandError):
			call_command(
				"import_articles_from_api",
				"https://api.example.com/articles/",
				"--target-org",
				"no-site-org",
			)
