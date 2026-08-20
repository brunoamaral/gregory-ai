"""
Unit tests for the author_profile_url template filter.

No database dependency.

Run:
  docker exec gregory python manage.py test gregory.tests.test_gregory_tags
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import SimpleTestCase
from gregory.templatetags.gregory_tags import author_profile_url, add_utm_params


class AuthorProfileUrlTest(SimpleTestCase):
	def test_no_orcid_returns_empty(self):
		self.assertEqual(author_profile_url("", "https://example.com/authors"), "")
		self.assertEqual(author_profile_url(None, "https://example.com/authors"), "")

	def test_no_base_falls_back_to_orcid_org(self):
		self.assertEqual(
			author_profile_url("0000-0002-7922-9785"),
			"https://orcid.org/0000-0002-7922-9785",
		)
		self.assertEqual(
			author_profile_url("0000-0002-7922-9785", ""),
			"https://orcid.org/0000-0002-7922-9785",
		)

	def test_base_without_trailing_slash(self):
		self.assertEqual(
			author_profile_url("0000-0002-7922-9785", "https://example.com/authors"),
			"https://example.com/authors/0000-0002-7922-9785/",
		)

	def test_base_with_trailing_slash(self):
		self.assertEqual(
			author_profile_url("0000-0002-7922-9785", "https://example.com/authors/"),
			"https://example.com/authors/0000-0002-7922-9785/",
		)


class AddUtmParamsTest(SimpleTestCase):
	"""
	add_utm_params used to (a) tag every URL regardless of host, leaking
	tracking params onto DOI resolvers and trial registries that Umami
	never sees, and (b) rebuild the whole query string via
	parse_qs()+urlencode(), which can silently rewrite '+', literal '%',
	or repeated keys that were never meant to be touched.
	"""

	def test_no_op_without_utm_params(self):
		self.assertEqual(
			add_utm_params("https://example.com/x", {}, "example.com"),
			"https://example.com/x",
		)
		self.assertEqual(add_utm_params("", {"utm_source": "a"}, "example.com"), "")

	def test_no_op_without_site_domain(self):
		# site_domain is required to tag anything — a caller that forgets
		# to pass it must fail closed (no tagging) rather than fall back
		# to tagging every host, which would reintroduce the leakage this
		# host restriction exists to prevent.
		result = add_utm_params(
			"https://example.com/articles/1/", {"utm_source": "weekly_summary"}
		)
		self.assertEqual(result, "https://example.com/articles/1/")
		result = add_utm_params(
			"https://example.com/articles/1/", {"utm_source": "weekly_summary"}, ""
		)
		self.assertEqual(result, "https://example.com/articles/1/")

	def test_appends_params_to_bare_url(self):
		result = add_utm_params(
			"https://example.com/articles/1/",
			{"utm_source": "weekly_summary", "utm_medium": "email"},
			"example.com",
		)
		self.assertIn("utm_source=weekly_summary", result)
		self.assertIn("utm_medium=email", result)
		self.assertTrue(result.startswith("https://example.com/articles/1/?"))

	def test_existing_query_string_preserved_byte_identical(self):
		# '+' must not become a space, '%20' must not be re-decoded/re-encoded
		# differently, and repeated keys must survive — all things
		# parse_qs()+urlencode() can silently rewrite.
		url = "https://example.com/x?q=a+b&note=100%25&tag=a&tag=b"
		result = add_utm_params(url, {"utm_source": "weekly_summary"}, "example.com")
		self.assertTrue(
			result.startswith(
				"https://example.com/x?q=a+b&note=100%25&tag=a&tag=b&"
			)
		)

	def test_only_appends_missing_keys(self):
		# utm_source is already present and must not be duplicated or
		# overridden; utm_medium is missing and must be appended.
		url = "https://example.com/x?utm_source=manual"
		result = add_utm_params(
			url,
			{"utm_source": "weekly_summary", "utm_medium": "email"},
			"example.com",
		)
		self.assertEqual(result.count("utm_source="), 1)
		self.assertIn("utm_source=manual", result)
		self.assertIn("utm_medium=email", result)

	def test_no_op_for_host_that_is_not_the_sending_site(self):
		# Trial registries, DOI resolvers, and the original publisher must
		# never be tagged — Umami can't see those hits, and the params
		# would only pollute a third party's analytics.
		result = add_utm_params(
			"https://clinicaltrials.gov/study/NCT123",
			{"utm_source": "weekly_summary"},
			"example.com",
		)
		self.assertEqual(result, "https://clinicaltrials.gov/study/NCT123")

	def test_tags_url_matching_site_domain(self):
		result = add_utm_params(
			"https://example.com/articles/1/",
			{"utm_source": "weekly_summary"},
			"example.com",
		)
		self.assertIn("utm_source=weekly_summary", result)

	def test_host_check_ignores_port(self):
		result = add_utm_params(
			"https://example.com:8443/articles/1/",
			{"utm_source": "weekly_summary"},
			"example.com",
		)
		self.assertIn("utm_source=weekly_summary", result)
