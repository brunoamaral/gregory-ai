import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.test import TestCase
from organizations.models import Organization
from api.models import APIAccessScheme


class APIAccessSchemeOrganizationFKTest(TestCase):
	"""Tests for the APIAccessScheme.organization FK introduced in PR 2."""

	def setUp(self):
		self.org = Organization.objects.create(name='Test Org', slug='test-org')

	def test_create_without_organization(self):
		"""Existing keys with no org (null) must still work."""
		scheme = APIAccessScheme.objects.create(
			client_name='No Org Client',
			client_contacts='noorg@example.com',
		)
		retrieved = APIAccessScheme.objects.get(pk=scheme.pk)
		self.assertIsNone(retrieved.organization)

	def test_create_with_organization(self):
		"""New keys can be bound to an organisation."""
		scheme = APIAccessScheme.objects.create(
			client_name='Org Client',
			client_contacts='org@example.com',
			organization=self.org,
		)
		retrieved = APIAccessScheme.objects.get(pk=scheme.pk)
		self.assertEqual(retrieved.organization, self.org)

	def test_organization_reverse_relation(self):
		"""org.api_access_schemes reverse manager works."""
		scheme = APIAccessScheme.objects.create(
			client_name='Reverse Rel Client',
			client_contacts='rev@example.com',
			organization=self.org,
		)
		self.assertIn(scheme, self.org.api_access_schemes.all())

	def test_null_org_does_not_break_existing_schemes(self):
		"""Rows created before the migration (simulated as null org) survive."""
		scheme = APIAccessScheme.objects.create(
			client_name='Legacy Client',
			client_contacts='legacy@example.com',
			organization=None,
		)
		self.assertIsNone(APIAccessScheme.objects.get(pk=scheme.pk).organization)

	def test_cascade_delete(self):
		"""Deleting an org cascades to its API keys."""
		org2 = Organization.objects.create(name='Doomed Org', slug='doomed-org')
		scheme = APIAccessScheme.objects.create(
			client_name='Doomed Client',
			client_contacts='doomed@example.com',
			organization=org2,
		)
		pk = scheme.pk
		org2.delete()
		self.assertFalse(APIAccessScheme.objects.filter(pk=pk).exists())
