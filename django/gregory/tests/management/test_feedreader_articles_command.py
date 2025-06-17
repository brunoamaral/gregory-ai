import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
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
	@patch('gregory.management.commands.feedreader_articles.Command.setup')
	@patch('gregory.management.commands.feedreader_articles.Command.update_articles_from_feeds')
	def test_handle_invokes_setup_and_update(self, mock_update, mock_setup):
		call_command('feedreader_articles')
		mock_setup.assert_called_once()
		mock_update.assert_called_once()

	@patch('gregory.management.commands.feedreader_articles.feedparser.parse')
	def test_fetch_feed_without_ssl(self, mock_parse):
		cmd = Command()
		mock_parse.return_value = {'entries': []}
		result = cmd.fetch_feed('http://example.com', False)
		mock_parse.assert_called_once_with('http://example.com')
		self.assertEqual(result, {'entries': []})

	@patch('gregory.management.commands.feedreader_articles.requests.get')
	@patch('gregory.management.commands.feedreader_articles.feedparser.parse')
	def test_fetch_feed_with_ssl_ignore(self, mock_parse, mock_get):
		cmd = Command()
		mock_get.return_value.content = b''
		mock_parse.return_value = {'entries': []}
		result = cmd.fetch_feed('http://example.com', True)
		mock_get.assert_called_once_with('http://example.com', verify=False)
		mock_parse.assert_called_once_with(b'')
		self.assertEqual(result, {'entries': []})

	def test_get_feed_processor(self):
		cmd = Command()
		self.assertIsInstance(cmd.get_feed_processor('https://pubmed.ncbi.nlm.nih.gov'), PubMedFeedProcessor)
		self.assertIsInstance(cmd.get_feed_processor('https://faseb.org/feed'), FasebFeedProcessor)
		self.assertIsInstance(cmd.get_feed_processor('https://biorxiv.org/rss'), BioRxivFeedProcessor)
		self.assertIsInstance(cmd.get_feed_processor('https://example.com/rss'), DefaultFeedProcessor)

	def test_keyword_filter_parsing_and_matching(self):
		processor = BioRxivFeedProcessor(Command())
		source = MagicMock(keyword_filter='cancer,"gene therapy"')
		entry_match = {'title': 'Gene Therapy Advances', 'summary': 'Cancer study'}
		entry_no_match = {'title': 'Other Research', 'summary': 'Nothing'}
		self.assertEqual(processor._parse_keyword_filter(source.keyword_filter), ['cancer', 'gene therapy'])
		self.assertTrue(processor.should_include_article(entry_match, source))
		self.assertFalse(processor.should_include_article(entry_no_match, source))
