import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.core.management import call_command
from django.test import TestCase
from unittest.mock import patch

class RebuildCategoriesCommandTest(TestCase):
	@patch('gregory.management.commands.rebuild_categories.Command.rebuild_cats_articles')
	@patch('gregory.management.commands.rebuild_categories.Command.rebuild_cats_trials')
	def test_handle_invokes_methods(self, mock_trials, mock_articles):
		call_command('rebuild_categories')
		mock_articles.assert_called_once()
		mock_trials.assert_called_once()
