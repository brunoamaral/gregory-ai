"""
Tests for the feedreader CrossRef reorder and the deferred keyword check
(pipeline audit, items 2.5 and 1.5).

Covers:
  - existing article with CrossRef data on file → no CrossRef call; feed-level
    facts (links, relationships) still merge
  - existing article never CrossRef-checked → refresh runs
  - new article → refresh runs
  - a feed-only update never blanks CrossRef-derived fields
  - keyword filter deferral: empty feed summary + DOI → decision made against
    the CrossRef abstract, with the fetched record reused (single call)

Run:
  docker exec gregory python manage.py test gregory.tests.test_feedreader_crossref_reorder
"""

import os
from unittest.mock import patch

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import TestCase
from django.utils import timezone

from gregory.classes import SciencePaper
from gregory.management.commands.feedreader_articles import Command
from gregory.models import Articles, Sources


def entry(**overrides):
	data = {
		"title": "Alpha study of something",
		"link": "https://ex.org/a",
		"published": "Mon, 05 Jan 2026 10:00:00 GMT",
		"dc_identifier": "doi:10.1000/alpha",
		"summary": "A feed summary",
	}
	data.update(overrides)
	return data


class CrossrefReorderTests(TestCase):
	def setUp(self):
		# PubMed source so PubMedFeedProcessor (with DOI extraction) handles it
		self.source = Sources.objects.create(
			name="PubMed feed",
			method="rss",
			source_for="science paper",
			active=True,
			link="https://pubmed.ncbi.nlm.nih.gov/rss/search/xyz",
		)
		self.cmd = Command()
		self.cmd.tzinfos = {}

	def run_feed(self, entries, refresh_side_effect=None):
		def default_refresh(paper_self):
			paper_self.abstract = paper_self.abstract or "CrossRef abstract text"
			return None

		with patch.object(
			Command, "fetch_feed", return_value={"entries": entries}
		), patch.object(
			SciencePaper,
			"refresh",
			autospec=True,
			side_effect=refresh_side_effect or default_refresh,
		) as mock_refresh:
			self.cmd.update_articles_from_feeds()
		return mock_refresh

	def test_existing_article_with_crossref_data_skips_refresh(self):
		article = Articles.objects.create(
			title="Alpha study of something",
			link="https://ex.org/a",
			doi="10.1000/alpha",
			crossref_check=timezone.now(),
			publisher="Nature Publishing",
		)
		mock_refresh = self.run_feed([entry()])

		mock_refresh.assert_not_called()
		article.refresh_from_db()
		# Feed-level facts still merged: source relationship and links
		self.assertIn(self.source, article.sources.all())
		self.assertTrue(article.links)

	def test_existing_article_without_crossref_check_is_refreshed(self):
		Articles.objects.create(
			title="Alpha study of something",
			link="https://ex.org/a",
			doi="10.1000/alpha",
			crossref_check=None,
		)
		mock_refresh = self.run_feed([entry()])
		self.assertEqual(mock_refresh.call_count, 1)

	def test_new_article_is_refreshed_and_created(self):
		mock_refresh = self.run_feed([entry()])
		self.assertEqual(mock_refresh.call_count, 1)
		self.assertEqual(Articles.objects.count(), 1)

	def test_feed_only_update_does_not_blank_crossref_fields(self):
		article = Articles.objects.create(
			title="Old feed title",
			link="https://ex.org/a",
			doi=None,
			summary="old summary",
			publisher="Nature Publishing",
			container_title="Nature",
			access="open",
			crossref_check=None,
		)
		# DOI-less entry matched by link; update path carries no CrossRef data
		self.run_feed(
			[
				entry(
					title="New feed title",
					dc_identifier="",
					summary="new summary",
				)
			]
		)
		article.refresh_from_db()
		self.assertEqual(article.title, "New feed title")
		self.assertEqual(article.publisher, "Nature Publishing")
		self.assertEqual(article.container_title, "Nature")
		self.assertEqual(article.access, "open")


class DeferredKeywordCheckTests(TestCase):
	def setUp(self):
		self.source = Sources.objects.create(
			name="PubMed filtered feed",
			method="rss",
			source_for="science paper",
			active=True,
			link="https://pubmed.ncbi.nlm.nih.gov/rss/search/xyz",
			keyword_filter="neuroplasticity",
		)
		self.cmd = Command()
		self.cmd.tzinfos = {}

	def run_feed(self, entries, abstract):
		def fake_refresh(paper_self):
			paper_self.abstract = abstract
			return None

		with patch.object(
			Command, "fetch_feed", return_value={"entries": entries}
		), patch.object(
			SciencePaper, "refresh", autospec=True, side_effect=fake_refresh
		) as mock_refresh:
			self.cmd.update_articles_from_feeds()
		return mock_refresh

	def test_abstract_keyword_match_includes_article_with_single_call(self):
		mock_refresh = self.run_feed(
			[entry(summary="")],
			abstract="This work shows neuroplasticity increases after therapy.",
		)
		self.assertEqual(Articles.objects.count(), 1)
		# The deferral's CrossRef fetch was reused for the creation: one call.
		self.assertEqual(mock_refresh.call_count, 1)

	def test_abstract_without_keyword_stays_excluded(self):
		self.run_feed(
			[entry(summary="")],
			abstract="Completely unrelated topic.",
		)
		self.assertEqual(Articles.objects.count(), 0)

	def test_crossref_failure_keeps_exclusion(self):
		def failing_refresh(paper_self):
			return "DOI not found"

		with patch.object(
			Command, "fetch_feed", return_value={"entries": [entry(summary="")]}
		), patch.object(
			SciencePaper, "refresh", autospec=True, side_effect=failing_refresh
		):
			self.cmd.update_articles_from_feeds()
		self.assertEqual(Articles.objects.count(), 0)

	def test_entry_with_real_summary_is_not_deferred(self):
		# Summary present and keyword absent → exclusion is final, no CrossRef call
		mock_refresh = self.run_feed(
			[entry(summary="A summary that does not match the filter")],
			abstract="neuroplasticity",
		)
		self.assertEqual(Articles.objects.count(), 0)
		mock_refresh.assert_not_called()

	def test_title_match_skips_deferral_entirely(self):
		mock_refresh = self.run_feed(
			[entry(title="Neuroplasticity and gait", summary="")],
			abstract="whatever",
		)
		self.assertEqual(Articles.objects.count(), 1)
		# One call: the creation path's refresh (entry was included directly).
		self.assertEqual(mock_refresh.call_count, 1)
