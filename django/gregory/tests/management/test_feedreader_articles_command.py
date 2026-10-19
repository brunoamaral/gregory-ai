import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.core.management import call_command
from django.test import TestCase
from unittest.mock import patch, MagicMock

from gregory.management.commands.feedreader_articles import (
	Command,
	PubMedFeedProcessor,
	FasebFeedProcessor,
	BioRxivFeedProcessor,
	DefaultFeedProcessor,
)


class FeedreaderArticlesCommandTest(TestCase):
	@patch("gregory.management.commands.feedreader_articles.Command.setup")
	@patch(
		"gregory.management.commands.feedreader_articles.Command.update_articles_from_feeds"
	)
	def test_handle_invokes_setup_and_update(self, mock_update, mock_setup):
		call_command("feedreader_articles")
		mock_setup.assert_called_once()
		mock_update.assert_called_once()

	@patch("gregory.management.commands.feedreader_articles.feedparser.parse")
	def test_fetch_feed_without_ssl(self, mock_parse):
		cmd = Command()
		mock_parse.return_value = {"entries": []}
		result = cmd.fetch_feed("http://example.com", False)
		mock_parse.assert_called_once_with("http://example.com")
		self.assertEqual(result, {"entries": []})

	@patch("gregory.management.commands.feedreader_articles.requests.get")
	@patch("gregory.management.commands.feedreader_articles.feedparser.parse")
	def test_fetch_feed_with_ssl_ignore(self, mock_parse, mock_get):
		cmd = Command()
		mock_get.return_value.content = b""
		mock_parse.return_value = {"entries": []}
		result = cmd.fetch_feed("http://example.com", True)
		mock_get.assert_called_once_with("http://example.com", verify=False, timeout=30)
		mock_parse.assert_called_once_with(b"")
		self.assertEqual(result, {"entries": []})

	def test_get_feed_processor(self):
		cmd = Command()
		self.assertIsInstance(
			cmd.get_feed_processor("https://pubmed.ncbi.nlm.nih.gov"),
			PubMedFeedProcessor,
		)
		self.assertIsInstance(
			cmd.get_feed_processor("https://faseb.org/feed"), FasebFeedProcessor
		)
		self.assertIsInstance(
			cmd.get_feed_processor("https://biorxiv.org/rss"), BioRxivFeedProcessor
		)
		self.assertIsInstance(
			cmd.get_feed_processor("https://example.com/rss"), DefaultFeedProcessor
		)

	def test_keyword_filter_parsing_and_matching(self):
		processor = BioRxivFeedProcessor(Command())
		source = MagicMock(keyword_filter='cancer,"gene therapy"')
		entry_match = {"title": "Gene Therapy Advances", "summary": "Cancer study"}
		entry_no_match = {"title": "Other Research", "summary": "Nothing"}
		self.assertEqual(
			processor._parse_keyword_filter(source.keyword_filter),
			["cancer", "gene therapy"],
		)
		self.assertTrue(processor.should_include_article(entry_match, source))
		self.assertFalse(processor.should_include_article(entry_no_match, source))

	def included(self, keyword_filter, title, summary=""):
		processor = DefaultFeedProcessor(Command())
		source = MagicMock(keyword_filter=keyword_filter)
		return processor.should_include_article({"title": title, "summary": summary}, source)

	def test_a_trailing_star_matches_words_that_start_with_the_keyword(self):
		for title in (
			"Oligodendrocyte precursors",
			"Oligodendroglia in repair",
			"New oligodendrocytes",
			"The (oligodendrocyte) lineage",
			"OLIGODENDRO",
		):
			with self.subTest(title):
				self.assertTrue(self.included("oligodendro*", title))

	def test_a_trailing_star_does_not_match_inside_a_word(self):
		self.assertFalse(self.included("oligodendro*", "Preoligodendrocyte signalling"))
		self.assertFalse(self.included("ms*", "Treatment of terms"))

	def test_a_starred_keyword_is_matched_in_the_summary_too(self):
		self.assertTrue(self.included("remyelinat*", "A paper", "Drives remyelination in mice"))

	def test_a_plain_keyword_still_matches_inside_a_word(self):
		self.assertTrue(self.included("oligodendro", "Preoligodendrocyte signalling"))
		self.assertFalse(self.included("oligodendro", "Nothing relevant"))

	def test_the_filter_from_production_sources_164_and_165_now_matches(self):
		keyword_filter = "oligodendro*, remyelinat*, NG2 glia, myelin"
		self.assertTrue(self.included(keyword_filter, "Oligodendrocyte maturation"))
		self.assertTrue(self.included(keyword_filter, "Promoting remyelination"))
		self.assertTrue(self.included(keyword_filter, "NG2 glia in the cortex"))
		self.assertFalse(self.included(keyword_filter, "Cardiac fibrosis"))

	def test_a_star_that_is_not_trailing_is_literal_and_a_lone_star_matches_nothing(self):
		self.assertFalse(self.included("oligo*dendro", "oligodendrocyte"))
		self.assertTrue(self.included("oligo*dendro", "the oligo*dendro case"))
		self.assertFalse(self.included("*", "anything at all"))
		self.assertFalse(self.included("**", "anything at all"))

	def test_a_quoted_phrase_can_end_in_a_star(self):
		self.assertTrue(self.included('"gene therap*"', "Gene therapies for ALS"))
		self.assertFalse(self.included('"gene therap*"', "Gene and therapy"))

	def test_keyword_in_text_is_shared_by_every_processor(self):
		for processor_class in (PubMedFeedProcessor, FasebFeedProcessor, BioRxivFeedProcessor, DefaultFeedProcessor):
			self.assertTrue(processor_class.keyword_in_text("myelin*", "a myelinating cell"))
