import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.core.management import call_command
from django.test import TestCase
from unittest.mock import patch

class FindDoiCommandTest(TestCase):
	@patch('gregory.management.commands.find_doi.Command.update_doi')
	def test_handle_calls_update_doi(self, mock_update):
		call_command('find_doi')
		mock_update.assert_called_once()
