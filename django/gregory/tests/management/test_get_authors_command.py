import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.core.management import call_command
from django.test import TestCase
from unittest.mock import patch, MagicMock

class GetAuthorsCommandTest(TestCase):
	@patch('gregory.management.commands.get_authors.load_dotenv')
	@patch('gregory.management.commands.get_authors.Works')
	@patch('gregory.management.commands.get_authors.CustomSetting')
	@patch('gregory.management.commands.get_authors.Articles')
	@patch('gregory.management.commands.get_authors.Authors')
	def test_handle_updates_authors(self, mock_authors, mock_articles, mock_setting, mock_works, mock_load):
		mock_setting.objects.get.return_value = MagicMock(site=MagicMock(domain='example.com'), title='Title', admin_email='admin@example.com')
		mock_articles.objects.filter.return_value = []
		call_command('get_authors')
		mock_load.assert_called_once()
		mock_works.assert_called_once()
		mock_setting.objects.get.assert_called_once()
