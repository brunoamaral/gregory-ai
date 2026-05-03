import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.test import TestCase
from organizations.models import Organization
from gregory.models import OrganizationApiSettings


class OrganizationApiSettingsRoundTripTest(TestCase):
	"""Basic create/read round-trip for OrganizationApiSettings."""

	def setUp(self):
		# Suppress the signal so we control creation manually in these tests.
		self.org = Organization.objects.create(name='Round Trip Org', slug='round-trip-org')
		# The signal will have created a row already; clean it up so the tests
		# can assert on explicit creation.
		OrganizationApiSettings.objects.filter(organization=self.org).delete()

	def test_create_with_default_false(self):
		settings = OrganizationApiSettings.objects.create(organization=self.org)
		self.assertFalse(settings.make_api_public)

	def test_create_explicit_true(self):
		settings = OrganizationApiSettings.objects.create(
			organization=self.org,
			make_api_public=True,
		)
		retrieved = OrganizationApiSettings.objects.get(pk=settings.pk)
		self.assertTrue(retrieved.make_api_public)

	def test_str_repr(self):
		settings = OrganizationApiSettings.objects.create(organization=self.org)
		self.assertIn(self.org.name, str(settings))

	def test_one_to_one_uniqueness(self):
		OrganizationApiSettings.objects.create(organization=self.org)
		from django.db import IntegrityError
		with self.assertRaises(IntegrityError):
			OrganizationApiSettings.objects.create(organization=self.org)


class OrganizationApiSettingsSignalTest(TestCase):
	"""post_save signal on Organization should auto-create an api_settings row."""

	def test_new_org_gets_settings_row(self):
		org = Organization.objects.create(name='Signal Org', slug='signal-org')
		self.assertTrue(
			OrganizationApiSettings.objects.filter(organization=org).exists()
		)

	def test_new_org_settings_defaults_to_false(self):
		org = Organization.objects.create(name='Private Org', slug='private-org')
		settings = OrganizationApiSettings.objects.get(organization=org)
		self.assertFalse(settings.make_api_public)

	def test_signal_is_idempotent(self):
		"""get_or_create in the signal means a second save doesn't create a duplicate."""
		org = Organization.objects.create(name='Idempotent Org', slug='idempotent-org')
		# Force another post_save (e.g. an org update)
		org.name = 'Idempotent Org Updated'
		org.save()
		self.assertEqual(
			OrganizationApiSettings.objects.filter(organization=org).count(), 1
		)


class OrganizationApiSettingsMigrationBackfillTest(TestCase):
	"""
	Verify the invariant that the migration's backfill step would leave
	one OrganizationApiSettings row per Organisation.

	The migration itself cannot be re-run in tests, but we can assert the
	invariant on the live test database state: every org created in tests
	(via the signal) has exactly one settings row.
	"""

	def test_every_org_has_exactly_one_settings_row(self):
		org1 = Organization.objects.create(name='Org One', slug='org-one')
		org2 = Organization.objects.create(name='Org Two', slug='org-two')
		for org in [org1, org2]:
			self.assertEqual(
				OrganizationApiSettings.objects.filter(organization=org).count(),
				1,
			)

	def test_count_parity(self):
		"""After creating several orgs, counts match."""
		before_orgs = Organization.objects.count()
		before_settings = OrganizationApiSettings.objects.count()
		self.assertEqual(before_orgs, before_settings)

		Organization.objects.create(name='Extra Org', slug='extra-org')
		self.assertEqual(
			Organization.objects.count(),
			OrganizationApiSettings.objects.count(),
		)
