import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import TestCase, RequestFactory
from django.contrib.sites.models import Site
from django.conf import settings
from organizations.models import Organization
from gregory.models import Team
from sitesettings.models import CustomSetting
from subscriptions.models import Lists, Subscribers
from subscriptions.views import (
	subscribe_view,
	_find_site_by_domain,
	_origin_matches_allowed,
	_check_origin_allowed,
	_resolve_site_from_request,
	_site_allowed_domains,
)


class SubscribeViewTest(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.org = Organization.objects.create(name="Test Org")
		self.team = Team.objects.create(
			organization=self.org, name="Alpha", slug="alpha"
		)
		Site.objects.update_or_create(
			id=settings.SITE_ID, defaults={"domain": "example.com", "name": "example"}
		)
		self.lst = Lists.objects.create(list_name="Daily", team=self.team)

	def test_subscribe_new_user(self):
		data = {
			"first_name": "Alice",
			"last_name": "Smith",
			"email": "ALICE@EXAMPLE.COM",
			"profile": "patient",
			"list": [str(self.lst.pk)],
		}
		request = self.factory.post("/subscribe/", data)
		response = subscribe_view(request)
		self.assertEqual(response.status_code, 302)
		self.assertIn("/thank-you/", response["Location"])
		subscriber = Subscribers.objects.get(email="alice@example.com")
		self.assertIn(self.lst, subscriber.subscriptions.all())

	def test_invalid_form(self):
		data = {
			"first_name": "",
			"last_name": "Smith",
			"email": "bademail",
			"profile": "patient",
			"list": [str(self.lst.pk)],
		}
		request = self.factory.post("/subscribe/", data)
		response = subscribe_view(request)
		self.assertEqual(response.status_code, 302)
		self.assertIn("/error/", response["Location"])

	def test_nonexistent_list_id_redirects_to_error(self):
		"""Posting a list ID that doesn't exist must redirect to /error/ and not create a subscriber."""
		data = {
			"first_name": "Bob",
			"last_name": "Jones",
			"email": "bob@example.com",
			"profile": "researcher",
			"list": ["99999"],  # does not exist
		}
		request = self.factory.post("/subscribe/", data)
		with self.assertLogs("subscriptions.views", level="ERROR") as log:
			response = subscribe_view(request)
		self.assertEqual(response.status_code, 302)
		self.assertIn("/error/", response["Location"])
		# No subscriber should have been created
		self.assertFalse(Subscribers.objects.filter(email="bob@example.com").exists())
		# Error was logged
		self.assertTrue(
			any("do not exist in the database" in msg for msg in log.output)
		)

	def test_mixed_valid_invalid_list_ids_redirects_to_error(self):
		"""If any submitted list ID is invalid the whole request should fail."""
		data = {
			"first_name": "Carol",
			"last_name": "White",
			"email": "carol@example.com",
			"profile": "doctor",
			"list": [str(self.lst.pk), "99999"],
		}
		request = self.factory.post("/subscribe/", data)
		with self.assertLogs("subscriptions.views", level="ERROR") as log:
			response = subscribe_view(request)
		self.assertEqual(response.status_code, 302)
		self.assertIn("/error/", response["Location"])
		self.assertFalse(Subscribers.objects.filter(email="carol@example.com").exists())

	def test_no_list_submitted_redirects_to_error(self):
		"""A form submitted without any list field must redirect to /error/."""
		data = {
			"first_name": "Dave",
			"last_name": "Black",
			"email": "dave@example.com",
			"profile": "patient",
			# no 'list' key
		}
		request = self.factory.post("/subscribe/", data)
		with self.assertLogs("subscriptions.views", level="ERROR") as log:
			response = subscribe_view(request)
		self.assertEqual(response.status_code, 302)
		self.assertIn("/error/", response["Location"])
		self.assertFalse(Subscribers.objects.filter(email="dave@example.com").exists())


# ---------------------------------------------------------------------------
# _find_site_by_domain
# ---------------------------------------------------------------------------


class FindSiteByDomainTest(TestCase):
	def setUp(self):
		Site.objects.update_or_create(
			id=settings.SITE_ID,
			defaults={"domain": "example.com", "name": "example"},
		)

	def test_exact_match(self):
		site = _find_site_by_domain("example.com")
		self.assertIsNotNone(site)
		self.assertEqual(site.domain, "example.com")

	def test_www_subdomain_resolves_to_parent(self):
		site = _find_site_by_domain("www.example.com")
		self.assertIsNotNone(site)
		self.assertEqual(site.domain, "example.com")

	def test_arbitrary_subdomain_resolves_to_parent(self):
		site = _find_site_by_domain("api.example.com")
		self.assertIsNotNone(site)
		self.assertEqual(site.domain, "example.com")

	def test_port_stripped_before_lookup(self):
		site = _find_site_by_domain("example.com:8080")
		self.assertIsNotNone(site)
		self.assertEqual(site.domain, "example.com")

	def test_unknown_domain_returns_none(self):
		self.assertIsNone(_find_site_by_domain("evil.com"))

	def test_unknown_subdomain_returns_none(self):
		self.assertIsNone(_find_site_by_domain("api.evil.com"))


# ---------------------------------------------------------------------------
# _origin_matches_allowed
# ---------------------------------------------------------------------------


class OriginMatchesAllowedTest(TestCase):
	def test_exact_match(self):
		self.assertTrue(_origin_matches_allowed("example.com", "example.com"))

	def test_exact_match_in_list(self):
		self.assertTrue(
			_origin_matches_allowed("example.com", "other.com, example.com")
		)

	def test_www_subdomain_matches_parent(self):
		self.assertTrue(_origin_matches_allowed("www.example.com", "example.com"))

	def test_arbitrary_subdomain_matches_parent(self):
		self.assertTrue(_origin_matches_allowed("api.example.com", "example.com"))

	def test_netloc_with_port_stripped(self):
		# _origin_matches_allowed also accepts raw netloc strings
		self.assertTrue(_origin_matches_allowed("example.com:443", "example.com"))

	def test_different_domain_no_match(self):
		self.assertFalse(_origin_matches_allowed("evil.com", "example.com"))

	def test_subdomain_of_wrong_domain_no_match(self):
		self.assertFalse(_origin_matches_allowed("example.evil.com", "example.com"))

	def test_empty_allowed_domains_no_match(self):
		self.assertFalse(_origin_matches_allowed("example.com", ""))


# ---------------------------------------------------------------------------
# _resolve_site_from_request
# ---------------------------------------------------------------------------


class ResolveSiteFromRequestTest(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		Site.objects.update_or_create(
			id=settings.SITE_ID,
			defaults={"domain": "example.com", "name": "example"},
		)

	def test_origin_header_exact(self):
		request = self.factory.post("/", HTTP_ORIGIN="https://example.com")
		self.assertEqual(_resolve_site_from_request(request).domain, "example.com")

	def test_origin_header_www_subdomain(self):
		request = self.factory.post("/", HTTP_ORIGIN="https://www.example.com")
		self.assertEqual(_resolve_site_from_request(request).domain, "example.com")

	def test_referer_header_used_when_no_origin(self):
		request = self.factory.post("/", HTTP_REFERER="https://example.com/subscribe/")
		self.assertEqual(_resolve_site_from_request(request).domain, "example.com")

	def test_origin_takes_precedence_over_referer(self):
		request = self.factory.post(
			"/",
			HTTP_ORIGIN="https://example.com",
			HTTP_REFERER="https://other.invalid/page",
		)
		self.assertEqual(_resolve_site_from_request(request).domain, "example.com")

	def test_falls_back_to_host_header(self):
		request = self.factory.post("/", SERVER_NAME="example.com", SERVER_PORT="80")
		self.assertEqual(_resolve_site_from_request(request).domain, "example.com")

	def test_unknown_origin_falls_back_to_default_site(self):
		# Origin and Host are both unregistered; falls back to Site.objects.get_current().
		request = self.factory.post("/", HTTP_ORIGIN="https://unknown.invalid")
		self.assertEqual(_resolve_site_from_request(request).domain, "example.com")


# ---------------------------------------------------------------------------
# Origin validation in subscribe_view
# ---------------------------------------------------------------------------


class OriginValidationTest(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.org = Organization.objects.create(name="Origin Validation Org")
		self.team = Team.objects.create(
			organization=self.org,
			name="OriginTeam",
			slug="origin-team",
		)
		self.site, _ = Site.objects.update_or_create(
			id=settings.SITE_ID,
			defaults={"domain": "example.com", "name": "example"},
		)
		self.custom = CustomSetting.objects.create(
			site=self.site,
			title="example settings",
			allowed_domains="partner.org",
		)
		self.lst = Lists.objects.create(
			list_name="Daily",
			team=self.team,
		)

	def _post(self, list_obj, email, origin=None, accept=None, ajax=False):
		headers = {}
		if origin is not None:
			headers["HTTP_ORIGIN"] = origin
		if accept is not None:
			headers["HTTP_ACCEPT"] = accept
		if ajax:
			headers["HTTP_X_REQUESTED_WITH"] = "XMLHttpRequest"
		return self.factory.post(
			"/subscribe/",
			{"first_name": "Test", "email": email, "list": [str(list_obj.pk)]},
			**headers,
		)

	def test_exact_site_domain_origin_succeeds(self):
		request = self._post(self.lst, "ok@example.com", "https://example.com")
		self.assertNotEqual(subscribe_view(request).status_code, 403)

	def test_www_subdomain_of_site_domain_succeeds(self):
		request = self._post(self.lst, "www@example.com", "https://www.example.com")
		self.assertNotEqual(subscribe_view(request).status_code, 403)

	def test_configured_allowed_domain_origin_succeeds(self):
		request = self._post(self.lst, "ok@partner.org", "https://partner.org")
		self.assertNotEqual(subscribe_view(request).status_code, 403)

	def test_malicious_origin_redirects_browser_to_error(self):
		"""A plain browser form POST from an unauthorized origin redirects to /error/."""
		request = self._post(self.lst, "hax@evil.com", "https://evil.com")
		with self.assertLogs("subscriptions.views", level="WARNING") as log:
			response = subscribe_view(request)
		self.assertEqual(response.status_code, 302)
		self.assertIn("/error/", response["Location"])
		self.assertFalse(Subscribers.objects.filter(email="hax@evil.com").exists())
		self.assertTrue(any("unauthorized origin" in msg for msg in log.output))

	def test_malicious_origin_returns_json_403_for_ajax(self):
		"""An XHR from an unauthorized origin gets a JSON 403."""
		request = self._post(
			self.lst,
			"hax2@evil.com",
			"https://evil.com",
			ajax=True,
		)
		with self.assertLogs("subscriptions.views", level="WARNING"):
			response = subscribe_view(request)
		self.assertEqual(response.status_code, 403)
		self.assertIn(b"Origin not permitted", response.content)

	def test_malicious_origin_returns_json_403_for_json_accept(self):
		"""A fetch() client sending Accept: application/json gets a JSON 403."""
		request = self._post(
			self.lst,
			"hax3@evil.com",
			"https://evil.com",
			accept="application/json",
		)
		with self.assertLogs("subscriptions.views", level="WARNING"):
			response = subscribe_view(request)
		self.assertEqual(response.status_code, 403)

	def test_no_origin_header_allowed_through_with_warning(self):
		"""Server-side / API requests without Origin are allowed (with a warning log)."""
		request = self._post(self.lst, "noorigin@example.com", origin=None)
		with self.assertLogs("subscriptions.views", level="WARNING") as log:
			response = subscribe_view(request)
		self.assertNotEqual(response.status_code, 403)
		self.assertTrue(any("no Origin or Referer header" in msg for msg in log.output))

	def test_blank_custom_setting_still_accepts_site_domain(self):
		"""With no configured allowed_domains, the site's own domain is still accepted."""
		self.custom.allowed_domains = ""
		self.custom.save(update_fields=["allowed_domains"])
		request = self._post(self.lst, "bare@example.com", "https://example.com")
		self.assertNotEqual(subscribe_view(request).status_code, 403)

	def test_blank_custom_setting_rejects_foreign_origin(self):
		"""With no configured allowed_domains, a foreign origin is rejected."""
		self.custom.allowed_domains = ""
		self.custom.save(update_fields=["allowed_domains"])
		request = self._post(self.lst, "hax@evil.com", "https://evil.com")
		with self.assertLogs("subscriptions.views", level="WARNING"):
			response = subscribe_view(request)
		self.assertEqual(response.status_code, 302)
		self.assertIn("/error/", response["Location"])

	def test_site_profile_recorded_from_origin_not_host(self):
		"""SubscriberSiteProfile.site must match the Origin header domain, not the API host."""
		from subscriptions.models import SubscriberSiteProfile

		request = self.factory.post(
			"/subscribe/",
			{
				"first_name": "Origin",
				"email": "originsite@example.com",
				"profile": "patient",
				"list": [str(self.lst.pk)],
			},
			HTTP_ORIGIN="https://example.com",
		)
		response = subscribe_view(request)
		self.assertNotEqual(response.status_code, 403)
		profile = SubscriberSiteProfile.objects.filter(
			subscriber__email="originsite@example.com",
		).first()
		self.assertIsNotNone(profile)
		self.assertEqual(profile.site.domain, "example.com")


# ---------------------------------------------------------------------------
# _site_allowed_domains / _check_origin_allowed
# ---------------------------------------------------------------------------


class SiteAllowedDomainsTest(TestCase):
	def setUp(self):
		self.site, _ = Site.objects.update_or_create(
			id=settings.SITE_ID,
			defaults={"domain": "example.com", "name": "example"},
		)

	def test_returns_site_domain_when_no_custom_setting(self):
		self.assertEqual(_site_allowed_domains(self.site), "example.com")

	def test_returns_site_domain_when_custom_setting_blank(self):
		CustomSetting.objects.create(site=self.site, title="bare", allowed_domains="")
		self.assertEqual(_site_allowed_domains(self.site), "example.com")

	def test_combines_site_domain_with_configured_domains(self):
		CustomSetting.objects.create(
			site=self.site,
			title="combined",
			allowed_domains="partner.org, friend.net",
		)
		combined = _site_allowed_domains(self.site)
		self.assertIn("example.com", combined)
		self.assertIn("partner.org", combined)
		self.assertIn("friend.net", combined)


class CheckOriginAllowedTest(TestCase):
	def setUp(self):
		self.site, _ = Site.objects.update_or_create(
			id=settings.SITE_ID,
			defaults={"domain": "example.com", "name": "example"},
		)

	def test_site_domain_is_always_allowed(self):
		self.assertTrue(_check_origin_allowed("example.com", self.site))

	def test_site_subdomain_is_allowed(self):
		self.assertTrue(_check_origin_allowed("www.example.com", self.site))

	def test_foreign_origin_rejected_without_custom_setting(self):
		self.assertFalse(_check_origin_allowed("evil.com", self.site))

	def test_configured_domain_is_allowed(self):
		CustomSetting.objects.create(
			site=self.site,
			title="cfg",
			allowed_domains="partner.org",
		)
		self.assertTrue(_check_origin_allowed("partner.org", self.site))

	def test_non_configured_domain_is_rejected(self):
		CustomSetting.objects.create(
			site=self.site,
			title="cfg",
			allowed_domains="partner.org",
		)
		self.assertFalse(_check_origin_allowed("evil.com", self.site))
