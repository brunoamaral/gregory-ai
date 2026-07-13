"""
Tests for the base FeedProcessor URL-based DOI fallback and the PubMed PMID
fallback (articles-missing-doi fix).

Covers:
  - FeedProcessor.extract_doi_with_fallback uses the subclass extract_doi
    result when present, and falls back to extract_doi_from_url otherwise
  - DefaultFeedProcessor-routed entries (the majority of the 226
    URL-embedded-DOI bucket) now resolve a DOI via the link
  - PubMedFeedProcessor.extract_doi resolves via PMID when dc:identifier is
    absent, and never calls the network when dc:identifier is present
  - process_feed_entry uses the fallback-aware extraction

Run:
	docker exec gregory python manage.py test gregory.tests.test_feedreader_doi_fallback
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from unittest.mock import MagicMock, patch

from django.test import TestCase

from gregory.management.commands.feedreader_articles import (
	Command,
	DefaultFeedProcessor,
	PubMedFeedProcessor,
	SpringerLinkFeedProcessor,
)


class ExtractDoiWithFallbackTests(TestCase):
	def setUp(self):
		self.mock_command = MagicMock()

	def test_uses_subclass_result_when_present(self):
		processor = SpringerLinkFeedProcessor(self.mock_command)
		entry = {
			"id": "10.1007/s00332-025-10234-8",
			"link": "https://link.springer.com/article/10.1007/s00332-025-10234-8",
		}
		self.assertEqual(
			processor.extract_doi_with_fallback(entry),
			"10.1007/s00332-025-10234-8",
		)

	def test_falls_back_to_url_when_subclass_yields_nothing(self):
		# DefaultFeedProcessor.extract_doi always returns None -- this is the
		# processor that every URL-embedded-DOI-but-unrecognized-feed article
		# (the 226-article bucket) was routed through.
		processor = DefaultFeedProcessor(self.mock_command)
		entry = {
			"title": "Some article",
			"link": "https://link.springer.com/article/10.1007/s10484-026-09800-x",
		}
		self.assertEqual(
			processor.extract_doi_with_fallback(entry),
			"10.1007/s10484-026-09800-x",
		)

	def test_doi_org_link_resolved_via_default_processor(self):
		processor = DefaultFeedProcessor(self.mock_command)
		entry = {"title": "Some article", "link": "https://doi.org/10.3109/xyz"}
		self.assertEqual(
			processor.extract_doi_with_fallback(entry), "10.3109/xyz"
		)

	def test_returns_none_when_neither_source_has_a_doi(self):
		processor = DefaultFeedProcessor(self.mock_command)
		entry = {
			"title": "Thesis repository entry",
			"link": "https://hdl.handle.net/10520/EJC-1234abcd",
		}
		self.assertIsNone(processor.extract_doi_with_fallback(entry))

	def test_missing_link_does_not_raise(self):
		processor = DefaultFeedProcessor(self.mock_command)
		entry = {"title": "No link here"}
		self.assertIsNone(processor.extract_doi_with_fallback(entry))


class PubMedExtractDoiTests(TestCase):
	def setUp(self):
		self.processor = PubMedFeedProcessor(MagicMock())

	def test_dc_identifier_present_never_calls_network(self):
		entry = {
			"dc_identifier": "doi:10.1234/example.doi",
			"link": "https://pubmed.ncbi.nlm.nih.gov/38812345/",
		}
		with patch("gregory.utils.doi_utils.requests.get") as mock_get:
			doi = self.processor.extract_doi(entry)
		self.assertEqual(doi, "10.1234/example.doi")
		mock_get.assert_not_called()

	def test_falls_back_to_pmid_resolution_when_dc_identifier_missing(self):
		entry = {"link": "https://pubmed.ncbi.nlm.nih.gov/38812345/"}
		payload = {
			"result": {
				"38812345": {
					"articleids": [{"idtype": "doi", "value": "10.5678/resolved.doi"}]
				}
			}
		}
		response = MagicMock()
		response.json.return_value = payload
		with patch(
			"gregory.utils.doi_utils.requests.get", return_value=response
		):
			doi = self.processor.extract_doi(entry)
		self.assertEqual(doi, "10.5678/resolved.doi")

	def test_network_failure_returns_none_not_raise(self):
		entry = {"link": "https://pubmed.ncbi.nlm.nih.gov/38812345/"}
		with patch(
			"gregory.utils.doi_utils.requests.get",
			side_effect=ConnectionError("down"),
		):
			doi = self.processor.extract_doi(entry)
		self.assertIsNone(doi)

	def test_no_pmid_in_link_returns_none_without_network_call(self):
		entry = {"link": "https://pubmed.ncbi.nlm.nih.gov/rss/search/xyz"}
		with patch("gregory.utils.doi_utils.requests.get") as mock_get:
			doi = self.processor.extract_doi(entry)
		self.assertIsNone(doi)
		mock_get.assert_not_called()


class ProcessFeedEntryUsesFallbackTests(TestCase):
	"""Integration-style check that process_feed_entry itself benefits from
	the fallback, not just extract_doi_with_fallback in isolation."""

	def setUp(self):
		self.command = Command()
		self.command.tzinfos = {}

	def test_default_processor_entry_gets_doi_from_link(self):
		from gregory.models import Sources, Team, Subject
		from organizations.models import Organization

		org = Organization.objects.create(name="Test Org")
		team = Team.objects.create(organization=org, slug="test-team")
		subject = Subject.objects.create(
			subject_name="Test Subject", subject_slug="test-subject", team=team
		)
		source = Sources.objects.create(
			name="Obscure Publisher Feed",
			method="rss",
			source_for="science paper",
			active=True,
			team=team,
			subject=subject,
			link="https://obscure-publisher.example/feed.xml",
		)
		processor = self.command.get_feed_processor(source.link)
		self.assertIsInstance(processor, DefaultFeedProcessor)

		entry = {
			"title": "An article with a URL-embedded DOI",
			"link": "https://link.springer.com/article/10.1007/s99999-026-00000-1",
			"published": "Mon, 05 Jan 2026 10:00:00 GMT",
			"summary": "A summary.",
		}

		from gregory.classes import SciencePaper

		def fake_refresh(paper_self):
			paper_self.abstract = "CrossRef abstract"
			return None

		with patch.object(
			SciencePaper, "refresh", autospec=True, side_effect=fake_refresh
		):
			self.command.process_feed_entry(entry, source, processor)

		from gregory.models import Articles

		article = Articles.objects.get(
			link="https://link.springer.com/article/10.1007/s99999-026-00000-1"
		)
		self.assertEqual(article.doi, "10.1007/s99999-026-00000-1")
