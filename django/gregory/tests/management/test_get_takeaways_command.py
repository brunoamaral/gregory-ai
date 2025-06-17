import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.core.management import call_command
from django.test import TestCase
from unittest.mock import patch, MagicMock
from gregory.management.commands.get_takeaways import Command

class GetTakeawaysCommandTest(TestCase):
	def test_clean_html(self):
		self.assertEqual(Command.clean_html('<b>hi</b>'), 'hi')

	def test_get_summary_max_length(self):
		text = 'word ' * 50
		self.assertEqual(Command.get_summary_max_length(text), 25)

	def test_summarize_abstract(self):
		mock_summarizer = MagicMock(return_value=[{'summary_text': 'ok'}])
		result = Command.summarize_abstract(1, 'word ' * 60, mock_summarizer, min_length=10)
		mock_summarizer.assert_called_once()
		self.assertEqual(result, 'ok')

	@patch('gregory.management.commands.get_takeaways.pipeline')
	@patch('gregory.management.commands.get_takeaways.Articles')
	def test_handle_processes_queryset(self, mock_articles, mock_pipeline):
		qs = [MagicMock(summary='test', article_id=1, save=MagicMock(), takeaways=None)]
		mock_articles.objects.annotate.return_value.filter.return_value = qs
		mock_pipeline.return_value = MagicMock(return_value=[{'summary_text': 'x'}])
		call_command('get_takeaways')
		mock_pipeline.assert_called_once_with('summarization', model='philschmid/bart-large-cnn-samsum')
