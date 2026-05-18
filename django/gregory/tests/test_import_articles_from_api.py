import os
from unittest.mock import patch, MagicMock

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')

import django
django.setup()

from django.test import TestCase
from django.core.management import call_command
from django.core.management.base import CommandError
from organizations.models import Organization
from gregory.models import Articles, ArticleOrgContent


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

	@patch("gregory.management.commands.import_articles_from_api.requests.get")
	def test_import_populates_article_org_content(self, mock_get):
		mock_get.return_value = _make_response([ARTICLE])

		call_command("import_articles_from_api", "https://api.example.com/articles/", "--target-org", "test-org")

		article = Articles.objects.get(title="Test Article")
		content = ArticleOrgContent.objects.get(article=article, organization=self.org)
		self.assertEqual(content.takeaways, "Key finding A.")
		self.assertEqual(content.summary_plain_english, "Simple explanation.")

	@patch("gregory.management.commands.import_articles_from_api.requests.get")
	def test_reimport_updates_existing_org_content(self, mock_get):
		mock_get.return_value = _make_response([ARTICLE])
		call_command("import_articles_from_api", "https://api.example.com/articles/", "--target-org", "test-org")

		updated = {**ARTICLE, "takeaways": "Updated takeaway.", "summary_plain_english": "Updated summary."}
		mock_get.return_value = _make_response([updated])
		call_command("import_articles_from_api", "https://api.example.com/articles/", "--target-org", "test-org")

		article = Articles.objects.get(title="Test Article")
		content = ArticleOrgContent.objects.get(article=article, organization=self.org)
		self.assertEqual(content.takeaways, "Updated takeaway.")
		self.assertEqual(content.summary_plain_english, "Updated summary.")
		self.assertEqual(ArticleOrgContent.objects.filter(article=article, organization=self.org).count(), 1)

	@patch("gregory.management.commands.import_articles_from_api.requests.get")
	def test_empty_takeaways_leaves_existing_row_untouched(self, mock_get):
		article = Articles.objects.create(title="Test Article", link="https://example.com/article/1")
		ArticleOrgContent.objects.create(
			article=article,
			organization=self.org,
			takeaways="Original takeaway.",
			summary_plain_english="Original summary.",
		)

		empty = {**ARTICLE, "takeaways": None, "summary_plain_english": None}
		mock_get.return_value = _make_response([empty])
		call_command("import_articles_from_api", "https://api.example.com/articles/", "--target-org", "test-org")

		content = ArticleOrgContent.objects.get(article=article, organization=self.org)
		self.assertEqual(content.takeaways, "Original takeaway.")
		self.assertEqual(content.summary_plain_english, "Original summary.")

	def test_missing_target_org_raises_command_error(self):
		with self.assertRaises((CommandError, SystemExit)):
			call_command("import_articles_from_api", "https://api.example.com/articles/")

	@patch("gregory.management.commands.import_articles_from_api.requests.get")
	def test_unknown_org_raises_command_error(self, mock_get):
		mock_get.return_value = _make_response([ARTICLE])
		with self.assertRaises(CommandError):
			call_command("import_articles_from_api", "https://api.example.com/articles/", "--target-org", "nonexistent-org")
