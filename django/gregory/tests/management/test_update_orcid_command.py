import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.core.management import call_command
from django.test import TestCase
from unittest.mock import patch, MagicMock

class UpdateOrcidCommandTest(TestCase):
	@patch('gregory.management.commands.update_orcid.get_orcid_credentials', return_value=('test_id', 'test_secret'))
	@patch('gregory.management.commands.update_orcid.Authors')
	@patch('gregory.management.commands.update_orcid.orcid.PublicAPI')
	def test_handle_calls_orcid_api(self, mock_public_api, mock_authors, mock_get_creds):
		instance = MagicMock()
		instance.get_search_token_from_orcid.return_value = 'tok'
		mock_public_api.return_value = instance
		auth_qs = MagicMock()
		auth_qs.filter.return_value = auth_qs
		auth_qs.order_by.return_value = auth_qs
		auth_qs.__getitem__.return_value = []
		mock_authors.objects.annotate.return_value = auth_qs
		call_command('update_orcid')
		mock_get_creds.assert_called_once_with(None)
		mock_public_api.assert_called_once_with('test_id', 'test_secret', sandbox=False)
		instance.get_search_token_from_orcid.assert_called_once()
