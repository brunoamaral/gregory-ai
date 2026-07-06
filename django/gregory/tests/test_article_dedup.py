"""
Tests for DOI-first article dedup and date-less entry ingestion
(pipeline audit, items 1.2 and 1.4).

Covers find_existing_article / create_or_update_article:
  - same title + different DOIs → two distinct articles (title is no longer
    globally unique)
  - DOI-less article matched by title gains the incoming DOI
  - DOI match wins even when the title changed
  - link fallback still matches rows whose stored title differs (PR #739 case)
  - date-less entries are ingested with published_date=None and never blank an
    existing date

Run:
  docker exec gregory python manage.py test gregory.tests.test_article_dedup
"""

import os
from datetime import datetime

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import TestCase

from gregory.management.commands.feedreader_articles import (
	Command,
	DefaultFeedProcessor,
)
from gregory.models import Articles, Sources


class ArticleDedupTests(TestCase):
	def setUp(self):
		self.source = Sources.objects.create(
			name="Feed",
			method="rss",
			source_for="science paper",
			active=True,
			link="https://feed.example/rss",
		)
		self.cmd = Command()

	def ingest(self, doi=None, title="Paper title", link="https://ex.org/a", **kwargs):
		return self.cmd.create_or_update_article(
			doi=doi,
			title=title,
			summary=kwargs.get("summary", "A summary"),
			link=link,
			published_date=kwargs.get("published_date"),
			source=self.source,
			crossref_check=kwargs.get("crossref_check"),
		)

	def test_same_title_different_dois_creates_two_articles(self):
		a1, created1, _ = self.ingest(doi="10.1000/aaa", link="https://ex.org/a")
		a2, created2, _ = self.ingest(doi="10.1000/bbb", link="https://ex.org/b")

		self.assertTrue(created1)
		self.assertTrue(created2)
		self.assertNotEqual(a1.pk, a2.pk)
		self.assertEqual(Articles.objects.filter(title="Paper title").count(), 2)

	def test_doiless_article_gains_incoming_doi(self):
		a1, created1, _ = self.ingest(doi=None, link="https://ex.org/a")
		self.assertTrue(created1)
		self.assertIsNone(a1.doi)

		a2, created2, _ = self.ingest(doi="10.1000/aaa", link="https://ex.org/other")
		self.assertFalse(created2)
		self.assertEqual(a1.pk, a2.pk)
		a1.refresh_from_db()
		self.assertEqual(a1.doi, "10.1000/aaa")

	def test_doi_match_wins_over_changed_title(self):
		a1, _, _ = self.ingest(doi="10.1000/aaa", title="Original title")
		a2, created2, _ = self.ingest(doi="10.1000/aaa", title="Corrected title")

		self.assertFalse(created2)
		self.assertEqual(a1.pk, a2.pk)
		a1.refresh_from_db()
		self.assertEqual(a1.title, "Corrected title")

	def test_link_fallback_matches_when_stored_title_differs(self):
		# Simulates a row ingested before title cleaning existed (PR #739):
		# same first-seen link, differently-formatted stored title.
		a1, _, _ = self.ingest(doi=None, title="Old <scp>TITLE</scp>", link="https://ex.org/a")
		a2, created2, _ = self.ingest(doi=None, title="Old TITLE", link="https://ex.org/a")

		self.assertFalse(created2)
		self.assertEqual(a1.pk, a2.pk)

	def test_existing_doi_is_never_overwritten_by_title_match(self):
		a1, _, _ = self.ingest(doi="10.1000/aaa", link="https://ex.org/a")
		a2, created2, _ = self.ingest(doi="10.1000/bbb", link="https://ex.org/b")

		a1.refresh_from_db()
		self.assertEqual(a1.doi, "10.1000/aaa")
		self.assertEqual(a2.doi, "10.1000/bbb")


class DatelessEntryTests(TestCase):
	def setUp(self):
		self.source = Sources.objects.create(
			name="Feed",
			method="rss",
			source_for="science paper",
			active=True,
			link="https://feed.example/rss",
		)
		self.cmd = Command()
		self.cmd.tzinfos = {}

	def test_extract_basic_fields_without_date_returns_none(self):
		processor = DefaultFeedProcessor(self.cmd)
		fields = processor.extract_basic_fields(
			{"title": "No date entry", "link": "https://ex.org/nodate"}
		)
		self.assertIsNone(fields["published_date"])
		self.assertEqual(fields["title"], "No date entry")

	def test_dateless_entry_is_ingested(self):
		article, created, _ = self.cmd.create_or_update_article(
			doi=None,
			title="No date entry",
			summary="s",
			link="https://ex.org/nodate",
			published_date=None,
			source=self.source,
		)
		self.assertTrue(created)
		self.assertIsNone(article.published_date)

	def test_dateless_update_does_not_blank_existing_date(self):
		published = datetime(2026, 1, 15, 12, 0)
		article, _, _ = self.cmd.create_or_update_article(
			doi=None,
			title="Dated entry",
			summary="s",
			link="https://ex.org/dated",
			published_date=published,
			source=self.source,
		)
		self.assertIsNotNone(article.published_date)

		# Same entry seen again, now without a date and with a changed summary
		article2, created2, _ = self.cmd.create_or_update_article(
			doi=None,
			title="Dated entry",
			summary="s (updated)",
			link="https://ex.org/dated",
			published_date=None,
			source=self.source,
		)
		self.assertFalse(created2)
		article2.refresh_from_db()
		self.assertIsNotNone(article2.published_date)
		self.assertEqual(article2.summary, "s (updated)")
