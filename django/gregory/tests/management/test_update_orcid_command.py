import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from unittest.mock import patch, MagicMock


class UpdateOrcidCommandTest(TestCase):
	@patch(
		"gregory.management.commands.update_orcid.get_orcid_credentials",
		return_value=("test_id", "test_secret"),
	)
	@patch("gregory.management.commands.update_orcid.Authors")
	@patch("gregory.management.commands.update_orcid.orcid.PublicAPI")
	@patch("django.apps.apps.get_model")
	def test_handle_calls_orcid_api(
		self, mock_get_model, mock_public_api, mock_authors, mock_get_creds
	):
		mock_org = MagicMock()
		mock_org.slug = "test-org"
		mock_org_class = MagicMock()
		mock_org_class.objects.get.return_value = mock_org
		mock_get_model.return_value = mock_org_class

		instance = MagicMock()
		instance.get_search_token_from_orcid.return_value = "tok"
		mock_public_api.return_value = instance

		auth_qs = MagicMock()
		auth_qs.annotate.return_value = auth_qs
		auth_qs.filter.return_value = auth_qs
		auth_qs.distinct.return_value = auth_qs
		auth_qs.order_by.return_value = auth_qs
		auth_qs.__getitem__.return_value = []
		auth_qs.__iter__ = MagicMock(return_value=iter([]))
		auth_qs.__len__ = MagicMock(return_value=0)
		mock_authors.objects.annotate.return_value = auth_qs

		call_command("update_orcid", organization="test-org")

		mock_get_creds.assert_called_once_with(organization=mock_org)
		mock_public_api.assert_called_once_with("test_id", "test_secret", sandbox=False)
		instance.get_search_token_from_orcid.assert_called_once()

	def test_handle_requires_organization_argument(self):
		with self.assertRaises(CommandError):
			call_command("update_orcid")

	@patch(
		"gregory.management.commands.update_orcid.get_orcid_credentials",
		return_value=(None, None),
	)
	@patch("django.apps.apps.get_model")
	def test_handle_exits_when_no_credentials(self, mock_get_model, mock_get_creds):
		mock_org = MagicMock()
		mock_org.slug = "no-creds-org"
		mock_org_class = MagicMock()
		mock_org_class.objects.get.return_value = mock_org
		mock_get_model.return_value = mock_org_class

		from io import StringIO

		err = StringIO()
		call_command("update_orcid", organization="no-creds-org", stderr=err)
		self.assertIn("ORCID credentials not found", err.getvalue())
