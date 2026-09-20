"""
Tests for the Authors admin's ability to search by a pasted CSV/newline list of
author_ids (gregory.admin.AuthorsAdmin.get_search_results).

Run with:
	docker exec gregory python manage.py test gregory.tests.test_admin_authors_search
"""

from django.contrib.admin.sites import AdminSite
from django.test import TestCase, RequestFactory

from gregory.admin import AuthorsAdmin
from gregory.models import Authors


class AuthorsIdListSearchTests(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.admin = AuthorsAdmin(Authors, AdminSite())
		self.a = Authors.objects.create(given_name="V Wee", family_name="Yong")
		self.b = Authors.objects.create(given_name="V. Wee", family_name="Yong", ORCID="0000-0001-5361-3327")
		self.c = Authors.objects.create(given_name="Voon Wee", family_name="Yong")
		self.other = Authors.objects.create(given_name="Ana", family_name="Silva")

	def _search(self, term):
		request = self.factory.get("/admin/gregory/authors/", {"q": term})
		queryset, may_have_duplicates = self.admin.get_search_results(
			request, Authors.objects.all(), term
		)
		return list(queryset), may_have_duplicates

	def test_comma_separated_id_list(self):
		term = f"{self.a.author_id},{self.c.author_id}"
		results, may_have_duplicates = self._search(term)

		self.assertCountEqual(results, [self.a, self.c])
		self.assertFalse(may_have_duplicates)

	def test_newline_separated_id_list(self):
		term = f"{self.a.author_id}\n{self.b.author_id}\n{self.c.author_id}"
		results, _ = self._search(term)

		self.assertCountEqual(results, [self.a, self.b, self.c])

	def test_mixed_commas_and_whitespace(self):
		term = f" {self.a.author_id} ,\n{self.b.author_id},  {self.c.author_id}\n"
		results, _ = self._search(term)

		self.assertCountEqual(results, [self.a, self.b, self.c])

	def test_nonexistent_ids_are_silently_ignored(self):
		term = f"{self.a.author_id},999999999"
		results, _ = self._search(term)

		self.assertCountEqual(results, [self.a])

	def test_name_search_falls_back_to_default_behavior(self):
		results, _ = self._search("Yong")

		self.assertCountEqual(results, [self.a, self.b, self.c])
		self.assertNotIn(self.other, results)

	def test_empty_search_falls_back_to_default_behavior(self):
		results, _ = self._search("   ")

		self.assertCountEqual(results, [self.a, self.b, self.c, self.other])

	def test_mixed_digits_and_letters_is_not_treated_as_id_list(self):
		term = f"{self.a.author_id}, Yong"
		results, _ = self._search(term)

		# Falls back to the default icontains search on "author_id, Yong" as a whole,
		# which won't match family_name/given_name/ORCID for any author.
		self.assertEqual(results, [])

	def test_signed_integer_is_not_treated_as_id_list(self):
		# Python's int() accepts a leading sign; a bare author_id never has one.
		results, _ = self._search(f"+{self.a.author_id}")

		self.assertEqual(results, [])

	def test_underscore_grouped_number_is_not_treated_as_id_list(self):
		# Python's int() accepts "_" as a digit-group separator; reject it too.
		results, _ = self._search("1_000")

		self.assertEqual(results, [])

	def test_id_beyond_autofield_range_falls_back_without_db_error(self):
		# Larger than Postgres's 32-bit integer max (2147483647): must not reach
		# the __in filter, where it would raise instead of matching nothing.
		results, _ = self._search("99999999999")

		self.assertEqual(results, [])
