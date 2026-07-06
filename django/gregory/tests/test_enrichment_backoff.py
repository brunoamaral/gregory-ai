"""
Tests for the enrichment backoff markers (pipeline audit, phase 2).

Contract under test, for find_doi / get_authors / update_articles_info:
  - an article with a future *_next_check is not selected
  - a fruitless COMPLETED attempt (API responded, nothing gained) sets
    attempts=1 and next_check ≈ now + 2 days; the second one ≈ +4 days
  - a network error advances nothing (immediate retry next run)
  - success clears the marker
  - get_authors never writes crossref_check (the old self-perpetuating loop)

Run:
  docker exec gregory python manage.py test gregory.tests.test_enrichment_backoff
"""

import os
from datetime import timedelta
from unittest.mock import MagicMock, patch

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from gregory.models import Articles
from gregory.utils.enrichment import backoff_delta


def assert_close(testcase, value, expected, tolerance_seconds=120):
	testcase.assertIsNotNone(value)
	testcase.assertLess(abs((value - expected).total_seconds()), tolerance_seconds)


class BackoffDeltaTests(TestCase):
	def test_schedule(self):
		self.assertEqual(backoff_delta(1), timedelta(days=2))
		self.assertEqual(backoff_delta(2), timedelta(days=4))
		self.assertEqual(backoff_delta(3), timedelta(days=8))
		self.assertEqual(backoff_delta(4), timedelta(days=16))
		self.assertEqual(backoff_delta(5), timedelta(days=30))
		self.assertEqual(backoff_delta(12), timedelta(days=30))


class FindDoiBackoffTests(TestCase):
	def make_article(self, **kwargs):
		defaults = {
			"title": f"Article {Articles.objects.count()}",
			"link": f"https://ex.org/{Articles.objects.count()}",
			"kind": "science paper",
		}
		defaults.update(kwargs)
		return Articles.objects.create(**defaults)

	def test_fruitless_attempt_backs_off(self):
		article = self.make_article()
		with patch(
			"gregory.management.commands.find_doi.greg.get_doi", return_value=None
		):
			call_command("find_doi")
		article.refresh_from_db()
		self.assertEqual(article.doi_lookup_attempts, 1)
		assert_close(self, article.doi_lookup_next_check, timezone.now() + timedelta(days=2))

		# Force the marker due again; second fruitless attempt doubles the wait.
		article.doi_lookup_next_check = timezone.now() - timedelta(minutes=1)
		article.save(update_fields=["doi_lookup_next_check"])
		with patch(
			"gregory.management.commands.find_doi.greg.get_doi", return_value=None
		):
			call_command("find_doi")
		article.refresh_from_db()
		self.assertEqual(article.doi_lookup_attempts, 2)
		assert_close(self, article.doi_lookup_next_check, timezone.now() + timedelta(days=4))

	def test_future_marker_is_not_selected(self):
		self.make_article(doi_lookup_next_check=timezone.now() + timedelta(days=10))
		with patch(
			"gregory.management.commands.find_doi.greg.get_doi", return_value=None
		) as mock_get:
			call_command("find_doi")
		mock_get.assert_not_called()

	def test_success_sets_doi_and_clears_marker(self):
		article = self.make_article(
			doi_lookup_attempts=3,
			doi_lookup_next_check=timezone.now() - timedelta(minutes=1),
		)
		with patch(
			"gregory.management.commands.find_doi.greg.get_doi",
			return_value="10.1000/found",
		):
			call_command("find_doi")
		article.refresh_from_db()
		self.assertEqual(article.doi, "10.1000/found")
		self.assertEqual(article.doi_lookup_attempts, 0)
		self.assertIsNone(article.doi_lookup_next_check)

	def test_network_error_advances_nothing(self):
		article = self.make_article()
		with patch(
			"gregory.management.commands.find_doi.greg.get_doi",
			side_effect=Exception("connection reset"),
		):
			call_command("find_doi")
		article.refresh_from_db()
		self.assertEqual(article.doi_lookup_attempts, 0)
		self.assertIsNone(article.doi_lookup_next_check)


@patch("gregory.management.commands.get_authors.CustomSetting")
@patch("gregory.management.commands.get_authors.Etiquette")
@patch("gregory.management.commands.get_authors.Works")
class GetAuthorsBackoffTests(TestCase):
	def make_article(self, **kwargs):
		defaults = {
			"title": f"Authored {Articles.objects.count()}",
			"link": f"https://ex.org/auth-{Articles.objects.count()}",
			"kind": "science paper",
			"doi": f"10.1000/auth-{Articles.objects.count()}",
			"crossref_check": timezone.now() - timedelta(days=400),
		}
		defaults.update(kwargs)
		return Articles.objects.create(**defaults)

	def run_with_crossref(self, works_cls, response):
		works = MagicMock()
		if isinstance(response, Exception):
			works.doi.side_effect = response
		else:
			works.doi.return_value = response
		works_cls.return_value = works
		call_command("get_authors")
		return works

	def test_record_without_authors_backs_off_and_leaves_crossref_check(
		self, works_cls, _etiquette, _setting
	):
		article = self.make_article()
		old_check = article.crossref_check
		self.run_with_crossref(works_cls, {"title": ["x"]})
		article.refresh_from_db()
		self.assertEqual(article.authors_attempts, 1)
		assert_close(self, article.authors_next_check, timezone.now() + timedelta(days=2))
		# The old code stamped crossref_check=now here, keeping the article
		# inside its own selection window forever.
		self.assertEqual(article.crossref_check, old_check)

	def test_authors_found_clears_marker(self, works_cls, _etiquette, _setting):
		article = self.make_article(
			authors_attempts=2,
			authors_next_check=timezone.now() - timedelta(minutes=1),
		)
		self.run_with_crossref(
			works_cls,
			{"author": [{"given": "Ada", "family": "Lovelace"}]},
		)
		article.refresh_from_db()
		self.assertEqual(article.authors.count(), 1)
		self.assertEqual(article.authors_attempts, 0)
		self.assertIsNone(article.authors_next_check)

	def test_future_marker_is_not_selected(self, works_cls, _etiquette, _setting):
		self.make_article(authors_next_check=timezone.now() + timedelta(days=10))
		works = self.run_with_crossref(works_cls, {"author": []})
		works.doi.assert_not_called()

	def test_network_error_advances_nothing(self, works_cls, _etiquette, _setting):
		article = self.make_article()
		self.run_with_crossref(works_cls, Exception("timeout"))
		article.refresh_from_db()
		self.assertEqual(article.authors_attempts, 0)
		self.assertIsNone(article.authors_next_check)


class UpdateArticlesInfoBackoffTests(TestCase):
	def make_article(self, **kwargs):
		defaults = {
			"title": f"Detail {Articles.objects.count()}",
			"link": f"https://ex.org/detail-{Articles.objects.count()}",
			"kind": "science paper",
			"doi": f"10.1000/detail-{Articles.objects.count()}",
			# access/publisher left NULL so the article is in the workset
		}
		defaults.update(kwargs)
		return Articles.objects.create(**defaults)

	def run_command(self, refresh_ok=True, paper_fields=None):
		"""Run update_articles_info with SciencePaper.refresh stubbed."""
		fields = paper_fields or {}

		def fake_refresh(paper_self):
			for key, value in fields.items():
				setattr(paper_self, key, value)

		refresh_patch = patch(
			"gregory.classes.SciencePaper.refresh", autospec=True
		)
		with refresh_patch as mock_refresh:
			if refresh_ok:
				mock_refresh.side_effect = fake_refresh
			else:
				import requests

				mock_refresh.side_effect = requests.exceptions.ConnectionError("down")
				# Avoid the 3x5s retry sleeps
				with patch("time.sleep"):
					call_command("update_articles_info")
					return
			call_command("update_articles_info")

	def test_fruitless_refresh_backs_off(self):
		article = self.make_article(summary="complete summary", access=None)
		self.run_command(refresh_ok=True, paper_fields={})
		article.refresh_from_db()
		self.assertEqual(article.details_attempts, 1)
		assert_close(self, article.details_next_check, timezone.now() + timedelta(days=2))

	def test_successful_refresh_clears_marker(self):
		article = self.make_article(
			summary="complete summary",
			access=None,
			details_attempts=2,
			details_next_check=timezone.now() - timedelta(minutes=1),
		)
		self.run_command(refresh_ok=True, paper_fields={"access": "open"})
		article.refresh_from_db()
		self.assertEqual(article.access, "open")
		self.assertEqual(article.details_attempts, 0)
		self.assertIsNone(article.details_next_check)

	def test_future_marker_is_not_selected(self):
		article = self.make_article(
			summary="complete summary",
			access=None,
			details_next_check=timezone.now() + timedelta(days=10),
		)
		self.run_command(refresh_ok=True, paper_fields={"access": "open"})
		article.refresh_from_db()
		self.assertIsNone(article.access)

	def test_network_failure_advances_nothing(self):
		article = self.make_article(summary="complete summary", access=None)
		self.run_command(refresh_ok=False)
		article.refresh_from_db()
		self.assertEqual(article.details_attempts, 0)
		self.assertIsNone(article.details_next_check)
