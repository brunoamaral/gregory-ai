"""
A site belongs to exactly one organisation (editorial API spec, PR 1).

`OrganizationSite` used to enforce only (organization, site) uniqueness, so one
site could be attached to two organisations and "the site's organisation" was
ambiguous. `unique_site_organization` makes it well defined.

Run with:
    docker exec gregory python manage.py test gregory.tests.test_organization_site_unique
"""

from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.forms import modelform_factory
from django.test import TestCase
from organizations.models import Organization

from gregory.models import OrganizationSite


class OrganizationSiteUniqueTests(TestCase):
	def setUp(self):
		self.org_a = Organization.objects.create(name="Org A", slug="org-a")
		self.org_b = Organization.objects.create(name="Org B", slug="org-b")
		self.site = Site.objects.create(domain="one.example.test", name="One")
		OrganizationSite.objects.create(organization=self.org_a, site=self.site)

	def test_second_organisation_for_same_site_is_rejected_by_db(self):
		with self.assertRaises(IntegrityError), transaction.atomic():
			OrganizationSite.objects.create(organization=self.org_b, site=self.site)

	def test_model_validation_gives_a_clean_error(self):
		duplicate = OrganizationSite(organization=self.org_b, site=self.site)
		with self.assertRaises(ValidationError):
			duplicate.full_clean()

	def test_admin_style_form_rejects_it(self):
		# The admin inline excludes `organization` from the form, which is the
		# shape that must still surface the site clash as a form error.
		Form = modelform_factory(OrganizationSite, fields=("site", "is_default"))
		form = Form(data={"site": self.site.pk, "is_default": ""})
		form.instance.organization = self.org_b
		self.assertFalse(form.is_valid())
		self.assertIn("site", form.errors)

	def test_different_sites_per_organisation_still_allowed(self):
		other = Site.objects.create(domain="two.example.test", name="Two")
		OrganizationSite.objects.create(organization=self.org_a, site=other)
		self.assertEqual(
			OrganizationSite.objects.filter(organization=self.org_a).count(), 2
		)
