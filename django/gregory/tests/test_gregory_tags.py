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
from gregory.templatetags.gregory_tags import author_profile_url


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
