"""
Tests for FeedProcessor.clean_title method.

Tests the title cleaning functionality that unescapes HTML entities,
preserves semantically meaningful inline tags (sub, sup, i, b, em, strong),
strips presentational/JATS tags while keeping their text, drops tag
attributes, and normalizes whitespace in feed article titles.
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import TestCase
from unittest.mock import MagicMock
from gregory.management.commands.feedreader_articles import PubMedFeedProcessor


class FeedTitleCleaningTest(TestCase):
	"""Test cases for FeedProcessor.clean_title static method."""

	def setUp(self):
		"""Set up test fixtures."""
		# Create a mock command instance (processor needs it for initialization)
		self.mock_command = MagicMock()
		self.processor = PubMedFeedProcessor(self.mock_command)

	def test_clean_title_real_world_pubmed_example(self):
		"""Test cleaning a real-world PubMed title: <scp> stripped, whitespace collapsed."""
		raw_title = (
			"<scp>KDM2B</scp>\n"
			"                    ‐\n"
			"                    <scp>PP1</scp>\n"
			"                    Promotes Remyelination and Functional Recovery After Facial Nerve Injury"
		)
		expected = "KDM2B ‐ PP1 Promotes Remyelination and Functional Recovery After Facial Nerve Injury"
		result = self.processor.clean_title(raw_title)
		self.assertEqual(result, expected)

	def test_clean_title_preserves_sub_tag(self):
		"""Test that <sub> tags are preserved (semantically meaningful)."""
		result = self.processor.clean_title("CO<sub>2</sub> capture")
		self.assertEqual(result, "CO<sub>2</sub> capture")

	def test_clean_title_preserves_italic_tag(self):
		"""Test that <i> tags are preserved (semantically meaningful)."""
		result = self.processor.clean_title("<i>Drosophila</i> genetics")
		self.assertEqual(result, "<i>Drosophila</i> genetics")

	def test_clean_title_strips_attributes_keeps_semantic_tag(self):
		"""Test that attributes are stripped from a preserved semantic tag."""
		result = self.processor.clean_title('<sub class="x" id="y">2</sub>')
		self.assertEqual(result, "<sub>2</sub>")

	def test_clean_title_unescapes_entities(self):
		"""Test that HTML entities like &amp; are unescaped to &."""
		result = self.processor.clean_title("Foo &amp; Bar")
		self.assertEqual(result, "Foo & Bar")

	def test_clean_title_plain_title_unchanged(self):
		"""Test that a plain title with no markup passes through unchanged."""
		plain_title = "Normal Article Title"
		result = self.processor.clean_title(plain_title)
		self.assertEqual(result, plain_title)

	def test_clean_title_empty_string(self):
		"""Test that empty string returns empty string."""
		result = self.processor.clean_title("")
		self.assertEqual(result, "")

	def test_clean_title_none_type(self):
		"""Test that None returns None."""
		result = self.processor.clean_title(None)
		self.assertIsNone(result)

	def test_clean_title_strips_presentational_keeps_semantic(self):
		"""Test mixed tags: <scp> stripped, <sub>/<sup>/<i> preserved."""
		result = self.processor.clean_title(
			"<scp>ABC</scp> <sub>x</sub> <sup>2</sup> <i>gene</i>"
		)
		self.assertEqual(result, "ABC <sub>x</sub> <sup>2</sup> <i>gene</i>")

	def test_keyword_filter_matches_across_markup_and_whitespace(self):
		"""Keyword phrases match even when the raw feed title has inline tags
		or pretty-printed newlines (regression for title-based filtering)."""
		source = MagicMock()
		source.keyword_filter = "facial nerve"
		entry = {
			"title": "Facial <scp>Nerve</scp>\n                    Injury Study",
			"summary": "",
		}
		self.assertTrue(self.processor.should_include_article(entry, source))
