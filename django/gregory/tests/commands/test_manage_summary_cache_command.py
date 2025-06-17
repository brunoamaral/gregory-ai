import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from io import StringIO
from django.core.management import call_command
from django.test import TestCase
from unittest.mock import patch

class ManageSummaryCacheCommandTest(TestCase):
	@patch('gregory.management.commands.manage_summary_cache.get_cache_stats')
	@patch('gregory.management.commands.manage_summary_cache._get_cache_file')
	def test_stats_option(self, mock_get_cache_file, mock_get_cache_stats):
		mock_get_cache_stats.return_value = {
			'cache_entries': 5,
			'hits': 3,
			'misses': 2,
			'hit_rate': 0.6
		}
		mock_get_cache_file.return_value = '/tmp/cache.db'
		with patch('os.path.exists', return_value=True), patch('os.path.getsize', return_value=1048576):
			out = StringIO()
			call_command('manage_summary_cache', '--stats', stdout=out)
		self.assertIn('Summary Cache Statistics', out.getvalue())
		self.assertIn('Cache entries: 5', out.getvalue())

	@patch('gregory.management.commands.manage_summary_cache.clear_cache')
	def test_clear_option(self, mock_clear_cache):
		mock_clear_cache.return_value = {'entries': 7}
		out = StringIO()
		call_command('manage_summary_cache', '--clear', stdout=out)
		self.assertIn('Cache cleared. 7 entries removed.', out.getvalue())
