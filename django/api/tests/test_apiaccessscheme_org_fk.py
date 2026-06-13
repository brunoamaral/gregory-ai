import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import TestCase
from organizations.models import Organization
from api.models import APIAccessScheme


class APIAccessSchemeOrganizationFKTest(TestCase):
	"""Tests for the APIAccessScheme.organization FK (PR 2) and required constraint (PR 8)."""

	def setUp(self):
		self.org = Organization.objects.create(name="Test Org", slug="test-org")

	def test_create_without_organization_raises(self):
		"""organization is required (PR 8); creating a key without it must raise IntegrityError."""
		from django.db import IntegrityError

		with self.assertRaises(IntegrityError):
			APIAccessScheme.objects.create(
				client_name="No Org Client",
				client_contacts="noorg@example.com",
				organization=None,
			)

	def test_create_with_organization(self):
		"""New keys can be bound to an organisation."""
		scheme = APIAccessScheme.objects.create(
			client_name="Org Client",
			client_contacts="org@example.com",
			organization=self.org,
		)
		retrieved = APIAccessScheme.objects.get(pk=scheme.pk)
		self.assertEqual(retrieved.organization, self.org)

	def test_organization_reverse_relation(self):
		"""org.api_access_schemes reverse manager works."""
		scheme = APIAccessScheme.objects.create(
			client_name="Reverse Rel Client",
			client_contacts="rev@example.com",
			organization=self.org,
		)
		self.assertIn(scheme, self.org.api_access_schemes.all())

	def test_all_schemes_have_organization(self):
		"""After PR 8, no APIAccessScheme row can have organization=None."""
		_scheme = APIAccessScheme.objects.create(
			client_name="Org-required Client",
			client_contacts="org@example.com",
			organization=self.org,
		)
		null_count = APIAccessScheme.objects.filter(organization__isnull=True).count()
		self.assertEqual(null_count, 0)

	def test_cascade_delete(self):
		"""Deleting an org cascades to its API keys."""
		org2 = Organization.objects.create(name="Doomed Org", slug="doomed-org")
		scheme = APIAccessScheme.objects.create(
			client_name="Doomed Client",
			client_contacts="doomed@example.com",
			organization=org2,
		)
		pk = scheme.pk
		org2.delete()
		self.assertFalse(APIAccessScheme.objects.filter(pk=pk).exists())
