"""
Tests for gregory/site_resolution.py (Phase 3 of site-scoped API visibility).

Covers:
  - find_site_by_domain: moved here from subscriptions/views.py (formerly
    _find_site_by_domain), same behaviour, same tests -- see that module's
    _resolve_site_from_request for its other consumer.
  - public_sites(): the shared code path api.views.PublicSitesView and
    NoSiteResolvedError's body both use, so GET /sites/ and the 400 it's
    quoted from can never disagree.
  - resolve_anonymous_site(): the ?site_id= -> Origin -> Referer -> None
    resolution order, and that every step considers ONLY api_public sites --
    the property that makes trusting a client-controlled Origin header safe
    (see the module's docstring). visible_subject_ids()'s own use of this
    (including the NoSiteResolvedError it raises) is pinned separately in
    gregory/tests/test_visibility_subjects.py, where fail-closed behaviour
    belongs alongside the rest of that function's per-caller rules.

Run with:
    docker exec gregory python manage.py test gregory.tests.test_site_resolution
"""

from django.conf import settings
from django.contrib.sites.models import Site
from django.test import RequestFactory, TestCase

from gregory.models import Team
from gregory.site_resolution import (
	find_site_by_domain,
	public_sites,
	resolve_anonymous_site,
)
from organizations.models import Organization
from sitesettings.models import CustomSetting


# ---------------------------------------------------------------------------
# find_site_by_domain
# ---------------------------------------------------------------------------


class FindSiteByDomainTest(TestCase):
	def setUp(self):
		Site.objects.update_or_create(
			id=settings.SITE_ID,
			defaults={"domain": "example.com", "name": "example"},
		)

	def test_exact_match(self):
		site = find_site_by_domain("example.com")
		self.assertIsNotNone(site)
		self.assertEqual(site.domain, "example.com")

	def test_www_subdomain_resolves_to_parent(self):
		site = find_site_by_domain("www.example.com")
		self.assertIsNotNone(site)
		self.assertEqual(site.domain, "example.com")

	def test_arbitrary_subdomain_resolves_to_parent(self):
		site = find_site_by_domain("api.example.com")
		self.assertIsNotNone(site)
		self.assertEqual(site.domain, "example.com")

	def test_port_stripped_before_lookup(self):
		site = find_site_by_domain("example.com:8080")
		self.assertIsNotNone(site)
		self.assertEqual(site.domain, "example.com")

	def test_unknown_domain_returns_none(self):
		self.assertIsNone(find_site_by_domain("evil.com"))

	def test_unknown_subdomain_returns_none(self):
		self.assertIsNone(find_site_by_domain("api.evil.com"))


# ---------------------------------------------------------------------------
# public_sites
# ---------------------------------------------------------------------------


class PublicSitesTest(TestCase):
	def test_only_api_public_sites_are_listed(self):
		org = Organization.objects.create(name="PS Org", slug="ps-org")
		Team.objects.create(organization=org, name="PS Team", slug="ps-team")

		pub_site = Site.objects.create(domain="ps-pub.test", name="Public")
		CustomSetting.objects.create(site=pub_site, title="Pub", api_public=True)

		priv_site = Site.objects.create(domain="ps-priv.test", name="Private")
		CustomSetting.objects.create(site=priv_site, title="Priv", api_public=False)

		result = public_sites()
		site_ids = {row["site_id"] for row in result}
		self.assertIn(pub_site.id, site_ids)
		self.assertNotIn(priv_site.id, site_ids)

	def test_row_shape(self):
		site = Site.objects.create(domain="ps-shape.test", name="Shape")
		CustomSetting.objects.create(site=site, title="Shape Setting", api_public=True)
		result = public_sites()
		row = next(r for r in result if r["site_id"] == site.id)
		self.assertEqual(row, {"site_id": site.id, "domain": site.domain, "name": site.name})

	def test_a_site_with_two_settings_rows_is_listed_once(self):
		"""CustomSetting.site is a plain FK, not OneToOne -- mirrors
		sitesettings' own test_lowest_setting_id_wins_when_multiple_rows."""
		site = Site.objects.create(domain="ps-dup.test", name="Dup")
		CustomSetting.objects.create(site=site, title="Dup A", api_public=True)
		CustomSetting.objects.create(site=site, title="Dup B", api_public=True)
		result = public_sites()
		matching = [r for r in result if r["site_id"] == site.id]
		self.assertEqual(len(matching), 1)


# ---------------------------------------------------------------------------
# resolve_anonymous_site
# ---------------------------------------------------------------------------


class ResolveAnonymousSiteTest(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.org = Organization.objects.create(name="RAS Org", slug="ras-org")
		Team.objects.create(organization=self.org, name="RAS Team", slug="ras-team")

		self.pub_site = Site.objects.create(domain="ras-pub.test", name="Public")
		CustomSetting.objects.create(site=self.pub_site, title="Pub", api_public=True)

		self.priv_site = Site.objects.create(domain="ras-priv.test", name="Private")
		CustomSetting.objects.create(site=self.priv_site, title="Priv", api_public=False)

	def test_site_id_resolves_a_public_site(self):
		request = self.factory.get("/", {"site_id": str(self.pub_site.pk)})
		site_id, varies, ambiguous = resolve_anonymous_site(request)
		self.assertEqual(site_id, self.pub_site.pk)
		self.assertFalse(varies)
		self.assertFalse(ambiguous)

	def test_site_id_for_a_private_site_falls_back_to_the_sole_public_site(self):
		"""Never widens: a private site's own id is not a candidate, so this
		falls through exactly as if no site_id had been given at all -- and
		with exactly one api_public site in this fixture, that fallback
		resolves to it (amended 2026-09-10; see module docstring), never to
		the private site that was actually asked for."""
		request = self.factory.get("/", {"site_id": str(self.priv_site.pk)})
		site_id, varies, ambiguous = resolve_anonymous_site(request)
		self.assertEqual(site_id, self.pub_site.pk)
		self.assertNotEqual(site_id, self.priv_site.pk)
		# Fell through to the Origin/Referer/public-union stage, which is
		# why this now "varies by origin" even though no Origin was sent.
		self.assertTrue(varies)
		self.assertFalse(ambiguous)

	def test_unknown_site_id_falls_back_to_the_sole_public_site(self):
		request = self.factory.get("/", {"site_id": "999999"})
		site_id, varies, ambiguous = resolve_anonymous_site(request)
		self.assertEqual(site_id, self.pub_site.pk)
		self.assertTrue(varies)
		self.assertFalse(ambiguous)

	def test_non_numeric_site_id_falls_back_to_the_sole_public_site(self):
		request = self.factory.get("/", {"site_id": "not-a-number"})
		site_id, varies, ambiguous = resolve_anonymous_site(request)
		self.assertEqual(site_id, self.pub_site.pk)
		self.assertTrue(varies)
		self.assertFalse(ambiguous)

	def test_origin_resolves_a_public_site_when_no_site_id_given(self):
		request = self.factory.get("/", HTTP_ORIGIN=f"https://{self.pub_site.domain}")
		site_id, varies, ambiguous = resolve_anonymous_site(request)
		self.assertEqual(site_id, self.pub_site.pk)
		self.assertTrue(varies)
		self.assertFalse(ambiguous)

	def test_malformed_bracketed_origin_does_not_crash(self):
		"""Origin/Referer are client-controlled; a malformed bracketed host
		makes urllib.parse's .hostname property raise ValueError instead of
		returning None. That must fall through to the next resolution step
		(here, the sole-public-site fallback), not surface as a 500."""
		request = self.factory.get("/", HTTP_ORIGIN="https://[::1")
		site_id, varies, ambiguous = resolve_anonymous_site(request)
		self.assertEqual(site_id, self.pub_site.pk)
		self.assertFalse(ambiguous)

	def test_malformed_bracketed_referer_does_not_crash(self):
		request = self.factory.get("/", HTTP_REFERER="https://[::1/page")
		site_id, varies, ambiguous = resolve_anonymous_site(request)
		self.assertEqual(site_id, self.pub_site.pk)
		self.assertFalse(ambiguous)

	def test_origin_spoofed_as_a_private_site_falls_back_to_the_sole_public_site(self):
		"""The single most important property in this phase: a caller
		claiming to come from a private site's domain must never be granted
		that site's scope -- resolution only ever considers api_public
		sites, at every step. With exactly one api_public site in this
		fixture, the fallback resolves to it rather than refusing outright
		(amended 2026-09-10) -- but it is that public site's scope, and
		never the private one the Origin claimed. See module docstring."""
		request = self.factory.get("/", HTTP_ORIGIN=f"https://{self.priv_site.domain}")
		site_id, varies, ambiguous = resolve_anonymous_site(request)
		self.assertEqual(site_id, self.pub_site.pk)
		self.assertNotEqual(site_id, self.priv_site.pk)
		self.assertTrue(varies)
		self.assertFalse(ambiguous)

	def test_referer_resolves_when_origin_is_absent(self):
		request = self.factory.get(
			"/", HTTP_REFERER=f"https://{self.pub_site.domain}/some/page/"
		)
		site_id, varies, ambiguous = resolve_anonymous_site(request)
		self.assertEqual(site_id, self.pub_site.pk)
		self.assertTrue(varies)
		self.assertFalse(ambiguous)

	def test_origin_takes_precedence_over_referer(self):
		request = self.factory.get(
			"/",
			HTTP_ORIGIN=f"https://{self.pub_site.domain}",
			HTTP_REFERER=f"https://{self.priv_site.domain}/page/",
		)
		site_id, varies, ambiguous = resolve_anonymous_site(request)
		self.assertEqual(site_id, self.pub_site.pk)
		self.assertFalse(ambiguous)

	def test_site_id_takes_precedence_over_origin(self):
		other_pub_site = Site.objects.create(domain="ras-pub-2.test", name="Public 2")
		CustomSetting.objects.create(site=other_pub_site, title="Pub 2", api_public=True)
		request = self.factory.get(
			"/",
			{"site_id": str(other_pub_site.pk)},
			HTTP_ORIGIN=f"https://{self.pub_site.domain}",
		)
		site_id, varies, ambiguous = resolve_anonymous_site(request)
		self.assertEqual(site_id, other_pub_site.pk)
		self.assertFalse(varies)
		self.assertFalse(ambiguous)

	def test_nothing_at_all_falls_back_to_the_sole_public_site(self):
		"""Amended 2026-09-10: the public union is data anyone may already
		read, and with exactly one api_public site here it just IS that
		site's scope -- so a caller naming nothing gets it directly rather
		than a 400. See test_two_or_more_public_sites_and_no_indicator_refuses
		below for the case this stops applying to."""
		request = self.factory.get("/")
		site_id, varies, ambiguous = resolve_anonymous_site(request)
		self.assertEqual(site_id, self.pub_site.pk)
		self.assertTrue(varies)
		self.assertFalse(ambiguous)

	def test_two_or_more_public_sites_and_no_indicator_refuses(self):
		"""The public union stops being unambiguous the moment a second
		api_public site exists -- serving it automatically would silently
		blend two sites' content into one anonymous response, so this is
		where resolution actually fails closed."""
		Site.objects.create(domain="ras-pub-2.test", name="Public 2")
		CustomSetting.objects.create(
			site=Site.objects.get(domain="ras-pub-2.test"),
			title="Pub 2",
			api_public=True,
		)
		request = self.factory.get("/")
		site_id, varies, ambiguous = resolve_anonymous_site(request)
		self.assertIsNone(site_id)
		self.assertTrue(varies)
		self.assertTrue(ambiguous)

	def test_two_or_more_public_sites_but_explicit_site_id_still_resolves(self):
		"""Ambiguity only applies to the fallback -- an explicit ?site_id=
		is never ambiguous, however many public sites exist."""
		other_pub_site = Site.objects.create(domain="ras-pub-3.test", name="Public 3")
		CustomSetting.objects.create(site=other_pub_site, title="Pub 3", api_public=True)
		request = self.factory.get("/", {"site_id": str(self.pub_site.pk)})
		site_id, varies, ambiguous = resolve_anonymous_site(request)
		self.assertEqual(site_id, self.pub_site.pk)
		self.assertFalse(varies)
		self.assertFalse(ambiguous)


class ResolveAnonymousSiteNoPublicSitesTest(TestCase):
	"""The other unambiguous case: zero api_public sites. The union is
	simply empty then, which is not an error -- see
	ResolveAnonymousSiteTest.test_two_or_more_public_sites_and_no_indicator_refuses
	for the case that IS."""

	def setUp(self):
		self.factory = RequestFactory()
		self.priv_site = Site.objects.create(domain="ras-nopub-priv.test", name="Private")
		CustomSetting.objects.create(site=self.priv_site, title="Priv", api_public=False)

	def test_nothing_at_all_resolves_to_no_site_without_erroring(self):
		request = self.factory.get("/")
		site_id, varies, ambiguous = resolve_anonymous_site(request)
		self.assertIsNone(site_id)
		self.assertFalse(ambiguous)
		# No Origin value could change this outcome -- there is nothing
		# public to resolve to, whatever the caller sends.
		self.assertFalse(varies)

	def test_origin_spoofed_as_the_private_site_still_does_not_error(self):
		request = self.factory.get("/", HTTP_ORIGIN=f"https://{self.priv_site.domain}")
		site_id, varies, ambiguous = resolve_anonymous_site(request)
		self.assertIsNone(site_id)
		self.assertFalse(ambiguous)
