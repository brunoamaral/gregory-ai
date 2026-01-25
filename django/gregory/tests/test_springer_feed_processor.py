"""
Tests for SpringerLinkFeedProcessor.

Tests the RSS feed processor for Springer Link (link.springer.com) feeds.
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.test import TestCase
from unittest.mock import MagicMock
from gregory.management.commands.feedreader_articles import SpringerLinkFeedProcessor


class SpringerLinkFeedProcessorTest(TestCase):
	"""Test cases for SpringerLinkFeedProcessor."""
	
	def setUp(self):
		"""Set up test fixtures."""
		# Create a mock command instance (processor needs it for initialization)
		self.mock_command = MagicMock()
		self.processor = SpringerLinkFeedProcessor(self.mock_command)
	
	def test_can_process_springer_link_url(self):
		"""Test that processor recognizes link.springer.com URLs."""
		self.assertTrue(
			self.processor.can_process('https://link.springer.com/search.rss?query=Multiple+Sclerosis')
		)
	
	def test_can_process_springer_link_url_with_params(self):
		"""Test that processor recognizes link.springer.com URLs with query parameters."""
		self.assertTrue(
			self.processor.can_process(
				'https://link.springer.com/search.rss?query=Multiple+Sclerosis&content-type=Article&date=m3&sortBy=newestFirst'
			)
		)
	
	def test_cannot_process_other_springer_domains(self):
		"""Test that processor does NOT match other Springer domains (strict matching)."""
		# Should not match springer.com without 'link.' prefix
		self.assertFalse(
			self.processor.can_process('https://www.springer.com/journal/123')
		)
		# Should not match nature.com (owned by Springer Nature)
		self.assertFalse(
			self.processor.can_process('https://www.nature.com/articles/s41467-025-61751-9')
		)
	
	def test_cannot_process_pubmed(self):
		"""Test that processor does not match PubMed URLs."""
		self.assertFalse(
			self.processor.can_process('https://pubmed.ncbi.nlm.nih.gov/rss/search/')
		)
	
	def test_extract_doi_from_guid(self):
		"""Test DOI extraction from GUID field."""
		entry = {
			'id': '10.1007/s00332-025-10234-8',
			'title': 'Test Article'
		}
		doi = self.processor.extract_doi(entry)
		self.assertEqual(doi, '10.1007/s00332-025-10234-8')
	
	def test_extract_doi_from_guid_field(self):
		"""Test DOI extraction when using 'guid' key instead of 'id'."""
		entry = {
			'guid': '10.1007/s40263-025-01246-9',
			'title': 'Multiple Sclerosis in Women'
		}
		doi = self.processor.extract_doi(entry)
		self.assertEqual(doi, '10.1007/s40263-025-01246-9')
	
	def test_extract_doi_validates_format(self):
		"""Test that DOI extraction validates the DOI starts with '10.'."""
		# Invalid DOI format
		entry = {
			'id': 'some-random-guid-12345',
			'title': 'Test Article'
		}
		doi = self.processor.extract_doi(entry)
		self.assertIsNone(doi)
	
	def test_extract_doi_empty_guid(self):
		"""Test DOI extraction returns None for empty GUID."""
		entry = {
			'id': '',
			'title': 'Test Article'
		}
		doi = self.processor.extract_doi(entry)
		self.assertIsNone(doi)
	
	def test_extract_doi_missing_guid(self):
		"""Test DOI extraction returns None when GUID is missing."""
		entry = {
			'title': 'Test Article'
		}
		doi = self.processor.extract_doi(entry)
		self.assertIsNone(doi)
	
	def test_extract_summary_with_html_tags(self):
		"""Test summary extraction strips HTML tags."""
		entry = {
			'description': '<p>This is the abstract with <b>bold</b> and <i>italic</i> text.</p>'
		}
		summary = self.processor.extract_summary(entry)
		self.assertEqual(summary, 'This is the abstract with bold and italic text.')
	
	def test_extract_summary_preserves_html_entities(self):
		"""Test summary extraction preserves HTML entities."""
		entry = {
			'description': '<p>The reaction A &amp; B produces C &gt; D.</p>'
		}
		summary = self.processor.extract_summary(entry)
		self.assertEqual(summary, 'The reaction A &amp; B produces C &gt; D.')
	
	def test_extract_summary_trims_whitespace(self):
		"""Test summary extraction trims and normalizes whitespace."""
		entry = {
			'description': '''<p>  This has
			multiple lines   and   extra   spaces.  </p>'''
		}
		summary = self.processor.extract_summary(entry)
		self.assertEqual(summary, 'This has multiple lines and extra spaces.')
	
	def test_extract_summary_empty_description(self):
		"""Test summary extraction handles empty descriptions."""
		entry = {
			'description': ''
		}
		summary = self.processor.extract_summary(entry)
		self.assertEqual(summary, '')
	
	def test_extract_summary_none_description(self):
		"""Test summary extraction handles None/missing descriptions."""
		entry = {}
		summary = self.processor.extract_summary(entry)
		self.assertEqual(summary, '')
	
	def test_extract_summary_fallback_to_summary_field(self):
		"""Test summary extraction falls back to 'summary' field if 'description' is empty."""
		entry = {
			'description': '',
			'summary': '<p>This is from the summary field.</p>'
		}
		summary = self.processor.extract_summary(entry)
		self.assertEqual(summary, 'This is from the summary field.')
	
	def test_extract_summary_complex_html(self):
		"""Test summary extraction with complex HTML structure."""
		entry = {
			'description': '''<p>Multiple sclerosis (MS) is a chronic, immune-mediated disorder 
			that predominantly affects women, with an average age of onset between 20 and 50 years. 
			As a result of the early age of onset and increasing life expectancies of women, 
			owing to improvements in disease-modifying treatments (DMTs), recommendations 
			regarding disease and symptom management may vary depending on their life stage 
			and should be tailored to the individual.</p>'''
		}
		summary = self.processor.extract_summary(entry)
		# Should be a single line with normalized spaces
		self.assertIn('Multiple sclerosis (MS) is a chronic', summary)
		self.assertIn('disease-modifying treatments (DMTs)', summary)
		self.assertNotIn('\n', summary)
		self.assertNotIn('\t', summary)
		self.assertNotIn('  ', summary)  # No double spaces
	
	def test_full_entry_processing(self):
		"""Test processing a complete Springer Link feed entry."""
		entry = {
			'title': 'Multiple Sclerosis in Women: Impact of Different Life Stages on Treatment Decisions',
			'description': '<p>Multiple sclerosis (MS) is a chronic, immune-mediated disorder that predominantly affects women.</p>',
			'link': 'https://link.springer.com/article/10.1007/s40263-025-01246-9',
			'id': '10.1007/s40263-025-01246-9',
			'published': '2026-01-24'
		}
		
		doi = self.processor.extract_doi(entry)
		summary = self.processor.extract_summary(entry)
		
		self.assertEqual(doi, '10.1007/s40263-025-01246-9')
		self.assertEqual(
			summary, 
			'Multiple sclerosis (MS) is a chronic, immune-mediated disorder that predominantly affects women.'
		)


class SpringerLinkFeedProcessorIntegrationTest(TestCase):
	"""Integration tests using real RSS feed structure."""
	
	def setUp(self):
		"""Set up test fixtures."""
		self.mock_command = MagicMock()
		self.processor = SpringerLinkFeedProcessor(self.mock_command)
	
	def test_parse_sample_rss_entries(self):
		"""Test parsing entries that match the example RSS structure."""
		# Simulating entries as they would appear after feedparser processes them
		entries = [
			{
				'title': 'Reaction–Diffusion Systems from Kinetic Models for Bacterial Communities on a Leaf Surface',
				'description': '<p>Many mathematical models for biological phenomena, such as the spread of diseases, are based on reaction–diffusion equations.</p>',
				'link': 'https://link.springer.com/article/10.1007/s00332-025-10234-8',
				'published': '2026-01-25',
				'id': '10.1007/s00332-025-10234-8'
			},
			{
				'title': 'Peripherin as a Biomarker in Neurodegenerative Diseases',
				'description': '',  # Empty description as seen in the example
				'link': 'https://link.springer.com/article/10.1007/s42399-026-02260-8',
				'published': '2026-01-24',
				'id': '10.1007/s42399-026-02260-8'
			}
		]
		
		# First entry - has description
		doi1 = self.processor.extract_doi(entries[0])
		summary1 = self.processor.extract_summary(entries[0])
		self.assertEqual(doi1, '10.1007/s00332-025-10234-8')
		self.assertIn('reaction–diffusion equations', summary1)
		
		# Second entry - empty description (will be fetched from CrossRef later)
		doi2 = self.processor.extract_doi(entries[1])
		summary2 = self.processor.extract_summary(entries[1])
		self.assertEqual(doi2, '10.1007/s42399-026-02260-8')
		self.assertEqual(summary2, '')  # Empty, CrossRef will fill this in later
