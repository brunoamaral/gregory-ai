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


class FeedBase(TestCase):
	"""A PubMed RSS source and a way to run one feed through the importer."""

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
		), patch.object(
			SciencePaper, "refresh", autospec=True, side_effect=refresh
		) as mock_refresh:
			self.cmd.update_articles_from_feeds()
		return mock_refresh


class FeedreaderPmidTests(FeedBase):
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


class RssFillsGapsOnlyAfterPubmedTests(FeedBase):
	"""Once an article carries PubMed's record, the RSS feed may only fill gaps."""

	def pubmed_article(self, **kwargs):
		defaults = dict(
			title="PubMed title",
			link="https://pubmed.ncbi.nlm.nih.gov/38812345/",
			summary="A complete structured abstract from PubMed. " * 10,
			doi="10.1000/alpha",
			pmid="38812345",
			pubmed_checked_at=timezone.now(),
			pubmed_status="MEDLINE",
		)
		defaults.update(kwargs)
		return Articles.objects.create(**defaults)

	def test_the_feed_does_not_replace_pubmeds_title_or_abstract(self):
		article = self.pubmed_article()
		full_summary = article.summary
		mock_refresh = self.run_feed([entry(title="Feed title", summary="Short feed text...")])
		article.refresh_from_db()
		self.assertEqual(article.title, "PubMed title")
		self.assertEqual(article.summary, full_summary)
		mock_refresh.assert_not_called()  # no CrossRef round-trip either
		self.assertIn(self.source, article.sources.all())
		self.assertEqual(Articles.objects.count(), 1)

	def test_the_feed_still_fills_a_gap(self):
		article = self.pubmed_article(summary="", published_date=None)
		mock_refresh = self.run_feed([entry(summary="Feed summary fills the gap")])
		article.refresh_from_db()
		self.assertEqual(article.summary, "Feed summary fills the gap")
		self.assertIsNotNone(article.published_date)
		self.assertEqual(article.title, "PubMed title")
		self.assertIn(PMID_LINK, (article.links or {}).values())
		mock_refresh.assert_not_called()

	def test_a_pubmed_article_without_a_doi_gains_the_feeds_doi_without_a_crossref_call(self):
		article = self.pubmed_article(doi=None)
		mock_refresh = self.run_feed([entry()])
		article.refresh_from_db()
		self.assertEqual(article.doi, "10.1000/alpha")
		self.assertEqual(article.title, "PubMed title")
		mock_refresh.assert_not_called()

	def test_a_missing_pmid_stamp_is_not_a_pubmed_record(self):
		# refresh_pubmed_records stamps pubmed_checked_at on PMIDs NCBI didn't return;
		# there is no PubMed title or abstract on such a row to protect.
		article = self.pubmed_article(pubmed_status=None)
		mock_refresh = self.run_feed([entry(title="Feed title")])
		mock_refresh.assert_called_once()
		article.refresh_from_db()
		self.assertEqual(article.title, "Feed title")

	def test_a_doi_less_feed_entry_also_only_fills_gaps(self):
		article = self.pubmed_article(doi=None)
		full_summary = article.summary
		with patch("gregory.utils.doi_utils.requests.get", side_effect=ConnectionError("down")):
			self.run_feed([entry(dc_identifier="", title="Feed title", summary="Short feed text")])
		article.refresh_from_db()
		self.assertEqual(article.title, "PubMed title")
		self.assertEqual(article.summary, full_summary)
		self.assertIn(self.source, article.sources.all())
		self.assertEqual(Articles.objects.count(), 1)

	def test_an_article_without_a_pubmed_record_still_takes_the_full_path(self):
		article = self.pubmed_article(pubmed_checked_at=None)
		mock_refresh = self.run_feed([entry(title="Feed title")])
		mock_refresh.assert_called_once()
		article.refresh_from_db()
		self.assertEqual(article.title, "Feed title")


class TitleMatchWithDifferentPmidTests(TestCase):
	def setUp(self):
		self.cmd = Command()

	def test_same_title_different_pmids_are_different_papers(self):
		Articles.objects.create(title="Erratum", link="https://x.org/1", pmid="111")
		self.assertIsNone(self.cmd.find_existing_article(None, "Erratum", "https://x.org/2", pmid="222"))

	def test_a_later_candidate_without_a_pmid_is_not_hidden_by_a_rejected_one(self):
		Articles.objects.create(title="Erratum", link="https://x.org/older")
		candidate = Articles.objects.get(link="https://x.org/older")
		Articles.objects.create(title="Erratum", link="https://x.org/newer", pmid="111")  # newer, sorts first
		found = self.cmd.find_existing_article(None, "Erratum", "https://x.org/new", pmid="222")
		self.assertEqual(found, candidate)

	def test_an_integer_pmid_is_compared_as_text(self):
		a = Articles.objects.create(title="Erratum", link="https://x.org/1", pmid="111")
		self.assertEqual(self.cmd.find_existing_article(None, "Erratum", "https://x.org/2", pmid=111), a)

	def test_a_candidate_without_a_pmid_still_matches_by_title(self):
		a = Articles.objects.create(title="Some title", link="https://x.org/1")
		found = self.cmd.find_existing_article(None, "Some title", "https://x.org/2", pmid="222")
		self.assertEqual(found, a)

	def test_a_feed_without_a_pmid_still_matches_by_title(self):
		a = Articles.objects.create(title="Some title", link="https://x.org/1", pmid="111")
		found = self.cmd.find_existing_article(None, "Some title", "https://x.org/2", pmid=None)
		self.assertEqual(found, a)
