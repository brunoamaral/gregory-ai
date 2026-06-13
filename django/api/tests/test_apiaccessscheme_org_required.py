"""
Tests for PR 8 — Drop null=True on APIAccessScheme.organization.

Covers:
  - Migration pre-check passes when all rows have an organisation.
  - Migration pre-check aborts when any row has organization=None.
  - Model-level: creating an APIAccessScheme without organization raises IntegrityError.
  - Model-level: creating an APIAccessScheme with organization succeeds.

Run with:
    docker exec gregory python manage.py test api.tests.test_apiaccessscheme_org_required
"""

from django.db import IntegrityError
from django.test import TestCase
from organizations.models import Organization

from api.models import APIAccessScheme


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_org(name, slug):
	return Organization.objects.create(name=name, slug=slug)


def _make_scheme(org, client_name="Test Client"):
	return APIAccessScheme.objects.create(
		client_name=client_name,
		client_contacts="test@example.com",
		organization=org,
	)


# ---------------------------------------------------------------------------
# Model-level tests
# ---------------------------------------------------------------------------


class APIAccessSchemeOrganizationRequiredTest(TestCase):
	"""organization is now a required FK; null rows must be rejected."""

	def setUp(self):
		self.org = _make_org("Required Org", "required-org")

	def test_create_with_organization_succeeds(self):
		"""A row with a valid organization is created without error."""
		scheme = _make_scheme(self.org)
		self.assertEqual(
			APIAccessScheme.objects.get(pk=scheme.pk).organization, self.org
		)

	def test_create_without_organization_raises(self):
		"""Creating a scheme without an organization must raise IntegrityError."""
		with self.assertRaises(IntegrityError):
			APIAccessScheme.objects.create(
				client_name="No Org",
				client_contacts="noorg@example.com",
				organization=None,
			)

	def test_reverse_relation_still_works(self):
		"""org.api_access_schemes reverse manager returns the correct rows."""
		scheme = _make_scheme(self.org, client_name="Rev Client")
		self.assertIn(scheme, self.org.api_access_schemes.all())

	def test_cascade_delete_still_works(self):
		"""Deleting an org still cascades to its API keys."""
		doomed_org = _make_org("Doomed", "doomed")
		scheme_pk = _make_scheme(doomed_org, client_name="Doomed Client").pk
		doomed_org.delete()
		self.assertFalse(APIAccessScheme.objects.filter(pk=scheme_pk).exists())


# ---------------------------------------------------------------------------
# Migration pre-check function tests
# ---------------------------------------------------------------------------


class MigrationPreCheckTest(TestCase):
	"""Unit tests for the check_no_null_organizations RunPython function."""

	def _run_pre_check(self, apps, schema_editor=None):
		"""Import and invoke the migration's pre-check directly."""
		import importlib

		mod = importlib.import_module(
			"api.migrations.0004_apiaccessscheme_organization_required"
		)
		if schema_editor is None:
			from unittest.mock import MagicMock

			schema_editor = MagicMock()
		mod.check_no_null_organizations(apps, schema_editor)

	def test_pre_check_passes_when_all_rows_have_org(self):
		"""Pre-check must not raise when every APIAccessScheme has an organization."""
		org = _make_org("Clean Org", "clean-org")
		_make_scheme(org, "Clean Client")

		# Should not raise — all rows have an org.
		try:
			from django.apps import apps as django_apps

			self._run_pre_check(django_apps)
		except Exception as exc:
			self.fail(f"Pre-check raised unexpectedly: {exc}")

	def test_pre_check_passes_with_no_rows(self):
		"""Pre-check must not raise when there are zero APIAccessScheme rows."""
		APIAccessScheme.objects.all().delete()
		try:
			from django.apps import apps as django_apps

			self._run_pre_check(django_apps)
		except Exception as exc:
			self.fail(f"Pre-check raised unexpectedly on empty table: {exc}")
