"""PMID handling in the RSS importer: PMID-first dedup and setting pmid on articles."""

from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from gregory.classes import SciencePaper
from gregory.management.commands.feedreader_articles import (
	Command,
	DefaultFeedProcessor,
	PubMedFeedProcessor,
)
from gregory.models import Articles, Sources

PMID_LINK = "https://pubmed.ncbi.nlm.nih.gov/38812345/"


def entry(**overrides):
	data = {
		"title": "Alpha study of something",
		"link": PMID_LINK,
		"published": "Mon, 05 Jan 2026 10:00:00 GMT",
		"dc_identifier": "doi:10.1000/alpha",
		"summary": "A feed summary",
	}
	data.update(overrides)
	return data


class ExtractPmidTests(TestCase):
	def test_pubmed_processor_reads_the_pmid_from_the_link(self):
		processor = PubMedFeedProcessor(MagicMock())
		self.assertEqual(processor.extract_pmid({"link": PMID_LINK}), "38812345")
		self.assertIsNone(processor.extract_pmid({"link": "https://example.org/x"}))
		self.assertIsNone(processor.extract_pmid({}))

	def test_other_processors_have_no_pmid(self):
		self.assertIsNone(DefaultFeedProcessor(MagicMock()).extract_pmid({"link": PMID_LINK}))


class FeedreaderPmidTests(TestCase):
	def setUp(self):
		self.source = Sources.objects.create(
			name="PubMed feed",
			method="rss",
			source_for="science paper",
			active=True,
			link="https://pubmed.ncbi.nlm.nih.gov/rss/search/xyz",
		)
		self.cmd = Command()
		self.cmd.tzinfos = {}

	def run_feed(self, entries):
		def refresh(paper_self):
			paper_self.abstract = paper_self.abstract or "CrossRef abstract text"
			return None

		with patch.object(
			Command, "fetch_feed", return_value={"entries": entries}
		), patch.object(SciencePaper, "refresh", autospec=True, side_effect=refresh):
			self.cmd.update_articles_from_feeds()

	def test_new_article_gets_the_pmid(self):
		self.run_feed([entry()])
		self.assertEqual(Articles.objects.get(doi="10.1000/alpha").pmid, "38812345")

	def test_new_doi_less_article_gets_the_pmid(self):
		with patch("gregory.utils.doi_utils.requests.get", side_effect=ConnectionError("down")):
			self.run_feed([entry(dc_identifier="")])
		self.assertEqual(Articles.objects.get().pmid, "38812345")

	def test_existing_article_is_found_by_pmid_before_doi(self):
		held = Articles.objects.create(
			title="Different title", link="https://other.org/1", pmid="38812345"
		)
		self.run_feed([entry()])
		self.assertEqual(Articles.objects.count(), 1)
		held.refresh_from_db()
		self.assertEqual(held.doi, "10.1000/alpha")

	def test_existing_article_found_by_doi_gains_the_pmid(self):
		article = Articles.objects.create(
			title="Alpha study of something",
			link="https://ex.org/a",
			doi="10.1000/alpha",
			crossref_check=timezone.now(),
		)
		self.run_feed([entry()])
		article.refresh_from_db()
		self.assertEqual(article.pmid, "38812345")

	def test_existing_pmid_is_not_overwritten(self):
		article = Articles.objects.create(
			title="Alpha study of something",
			link="https://ex.org/a",
			doi="10.1000/alpha",
			crossref_check=timezone.now(),
			pmid="111",
		)
		self.run_feed([entry()])
		article.refresh_from_db()
		self.assertEqual(article.pmid, "111")


	def test_pmid_row_without_doi_does_not_steal_a_doi_held_elsewhere(self):
		Articles.objects.create(title="Holder", link="https://ex.org/h", pmid="38812345")
		doi_row = Articles.objects.create(
			title="Other", link="https://ex.org/a", doi="10.1000/alpha"
		)
		self.run_feed([entry()])
		self.assertEqual(Articles.objects.count(), 2)
		doi_row.refresh_from_db()
		self.assertIsNone(doi_row.pmid)
		self.assertIsNone(Articles.objects.get(pmid="38812345").doi)

	def test_pmid_row_with_a_different_doi_is_not_overwritten(self):
		other = Articles.objects.create(
			title="Corrected version", link="https://ex.org/o", doi="10.1000/other",
			summary="Original summary", pmid="38812345",
		)
		self.run_feed([entry()])
		other.refresh_from_db()
		self.assertEqual(other.doi, "10.1000/other")
		self.assertEqual(other.summary, "Original summary")
		new = Articles.objects.get(doi="10.1000/alpha")
		self.assertNotEqual(new.pk, other.pk)
		self.assertIsNone(new.pmid)

	def test_pmid_claimed_meanwhile_costs_only_the_pmid(self):
		article = Articles.objects.create(title="T", link="https://ex.org/t", doi="10.1000/alpha")
		cmd = Command()

		real_filter = Articles.objects.filter

		def claimed_after_check(*args, **kwargs):
			result = real_filter(*args, **kwargs)
			if kwargs.get("pmid") == "555" and "_raced" not in claimed_after_check.__dict__:
				claimed_after_check._raced = True
				# the check sees no holder; then someone else takes the PMID
				Articles.objects.create(title="Racer", link="https://ex.org/r", pmid="555")
			return result

		with patch.object(Articles.objects, "filter", side_effect=claimed_after_check):
			self.assertFalse(cmd._claim_pmid(article, "555"))
		article.refresh_from_db()
		self.assertIsNone(article.pmid)
