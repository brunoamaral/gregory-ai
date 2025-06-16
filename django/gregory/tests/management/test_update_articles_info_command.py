import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.core.management import call_command
from django.test import TestCase
from unittest.mock import patch

class UpdateArticlesInfoCommandTest(TestCase):
	@patch('gregory.management.commands.update_articles_info.Command.update_article_details')
	def test_handle_calls_update_article_details(self, mock_update):
		call_command('update_articles_info')
		mock_update.assert_called_once()
