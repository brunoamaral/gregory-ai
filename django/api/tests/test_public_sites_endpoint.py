"""Tests for GET /sites/ — the site-discovery endpoint.

Site-scoped API visibility fails closed when a caller names no site, which
leaves a new consumer unable to call anything: it needs a site_id, and nothing
else would tell it which exist. This endpoint breaks that circle, so it is
deliberately unscoped and unauthenticated. That makes it a boundary worth
pinning: everything it returns is public by construction, and anything it
leaks is leaked to everyone.
"""

from django.contrib.sites.models import Site
from django.test import TestCase
from rest_framework.test import APIClient

from sitesettings.models import CustomSetting


class PublicSitesEndpointTests(TestCase):
	def setUp(self):
		self.client = APIClient()

		self.public_site = Site.objects.create(
			domain="pse-public.example.test", name="Public Site"
		)
		CustomSetting.objects.create(
			site=self.public_site, title="PSE Public", api_public=True
		)

		self.private_site = Site.objects.create(
			domain="pse-private.example.test", name="Private Site"
		)
		CustomSetting.objects.create(
			site=self.private_site, title="PSE Private", api_public=False
		)

	def test_anonymous_callers_may_read_it(self):
		"""Unauthenticated access is the whole point — a caller with no
		credential and no site_id has to be able to bootstrap from here."""
		response = self.client.get("/sites/")
		self.assertEqual(response.status_code, 200)

	def test_lists_public_sites_with_the_documented_shape(self):
		response = self.client.get("/sites/")
		row = next(
			r for r in response.data if r["site_id"] == self.public_site.id
		)
		self.assertEqual(
			set(row.keys()), {"site_id", "domain", "name"}
		)
		self.assertEqual(row["domain"], "pse-public.example.test")
		self.assertEqual(row["name"], "Public Site")

	def test_excludes_sites_that_are_not_api_public(self):
		"""api_public defaults to False, so a site is absent until someone
		publishes it deliberately."""
		response = self.client.get("/sites/")
		returned = {r["site_id"] for r in response.data}
		self.assertIn(self.public_site.id, returned)
		self.assertNotIn(self.private_site.id, returned)

	def test_a_site_with_several_settings_rows_appears_once(self):
		"""CustomSetting.site is a plain FK, not OneToOne — sitesettings
		already handles multiple rows per site elsewhere (see
		test_lowest_setting_id_wins_when_multiple_rows). Iterating settings
		rather than sites would hand clients duplicate site_ids, which
		contradicts what this endpoint is for."""
		CustomSetting.objects.create(
			site=self.public_site, title="PSE Public Second", api_public=True
		)

		response = self.client.get("/sites/")
		ids = [r["site_id"] for r in response.data]
		self.assertEqual(
			ids.count(self.public_site.id),
			1,
			f"expected one row per site, got {ids}",
		)
		self.assertEqual(len(ids), len(set(ids)), "site_ids must be unique")

	def test_a_site_public_on_one_row_and_private_on_another_is_listed(self):
		"""Pins the tie-break: any api_public row publishes the site. Worth
		asserting rather than leaving to the dedup's ordering, because the
		alternative reading — private wins — is equally defensible and this
		is the one implemented."""
		CustomSetting.objects.create(
			site=self.private_site, title="PSE Private Second", api_public=True
		)

		response = self.client.get("/sites/")
		returned = {r["site_id"] for r in response.data}
		self.assertIn(self.private_site.id, returned)
