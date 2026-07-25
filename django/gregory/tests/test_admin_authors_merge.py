"""
Tests for the Authors admin's "Merge selected authors" bulk action
(gregory.admin.AuthorsAdmin.merge_selected_authors).

Run with:
    docker exec gregory python manage.py test gregory.tests.test_admin_authors_merge
"""

from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import TestCase, RequestFactory

from gregory.admin import AuthorsAdmin
from gregory.models import Articles, Authors


class AuthorsMergeActionTests(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.admin = AuthorsAdmin(Authors, AdminSite())

	def _request(self, post_data=None):
		if post_data is not None:
			request = self.factory.post("/admin/gregory/authors/", post_data)
		else:
			request = self.factory.get("/admin/gregory/authors/")
		request.user = None
		request.session = {}
		request._messages = FallbackStorage(request)
		return request

	def test_confirmation_page_rendered_without_apply(self):
		a = Authors.objects.create(given_name="Ana", family_name="Silva", ORCID="0000-0002-1825-0097")
		b = Authors.objects.create(
			given_name="A.", family_name="Silva", ORCID="https://orcid.org/0000-0002-1825-0097"
		)

		request = self._request()
		queryset = Authors.objects.filter(pk__in=[a.pk, b.pk])
		response = self.admin.merge_selected_authors(request, queryset)

		self.assertEqual(response.status_code, 200)
		self.assertTrue(Authors.objects.filter(pk=a.pk).exists())
		self.assertTrue(Authors.objects.filter(pk=b.pk).exists())

	def test_merge_requires_at_least_two_authors(self):
		a = Authors.objects.create(given_name="Ana", family_name="Silva")

		request = self._request()
		queryset = Authors.objects.filter(pk=a.pk)
		response = self.admin.merge_selected_authors(request, queryset)

		self.assertIsNone(response)
		self.assertTrue(Authors.objects.filter(pk=a.pk).exists())

	def test_merge_rejects_conflicting_orcids(self):
		a = Authors.objects.create(given_name="Ana", family_name="Silva", ORCID="0000-0002-1825-0097")
		b = Authors.objects.create(given_name="Ana", family_name="Souza", ORCID="0000-0003-1111-2222")

		request = self._request(post_data={"apply": "1", "keep_author": str(a.pk)})
		queryset = Authors.objects.filter(pk__in=[a.pk, b.pk])
		response = self.admin.merge_selected_authors(request, queryset)

		self.assertIsNone(response)
		self.assertTrue(Authors.objects.filter(pk=a.pk).exists())
		self.assertTrue(Authors.objects.filter(pk=b.pk).exists())

	def test_merge_matches_bare_id_against_url_variant(self):
		kept = Authors.objects.create(
			given_name="Ana", family_name="Silva", ORCID="https://orcid.org/0000-0002-1825-0097"
		)
		duplicate = Authors.objects.create(
			given_name="A.", family_name="Silva", ORCID="0000-0002-1825-0097"
		)
		article = Articles.objects.create(title="Paper", link="https://example.com/paper")
		article.authors.add(duplicate)

		request = self._request(post_data={"apply": "1", "keep_author": str(kept.pk)})
		queryset = Authors.objects.filter(pk__in=[kept.pk, duplicate.pk])
		response = self.admin.merge_selected_authors(request, queryset)

		self.assertIsNone(response)
		kept.refresh_from_db()
		self.assertEqual(kept.ORCID, "0000-0002-1825-0097")
		self.assertFalse(Authors.objects.filter(pk=duplicate.pk).exists())
		self.assertIn(kept, article.authors.all())

	def test_merge_allows_blank_orcid_on_one_side(self):
		kept = Authors.objects.create(given_name="Ana", family_name="Silva", ORCID="0000-0002-1825-0097")
		duplicate = Authors.objects.create(given_name="A.", family_name="Silva", ORCID="")

		request = self._request(post_data={"apply": "1", "keep_author": str(kept.pk)})
		queryset = Authors.objects.filter(pk__in=[kept.pk, duplicate.pk])
		response = self.admin.merge_selected_authors(request, queryset)

		self.assertIsNone(response)
		kept.refresh_from_db()
		self.assertEqual(kept.ORCID, "0000-0002-1825-0097")
		self.assertFalse(Authors.objects.filter(pk=duplicate.pk).exists())

	def test_merge_honors_chosen_keep_author(self):
		a = Authors.objects.create(given_name="Ana", family_name="Silva", ORCID="0000-0002-1825-0097")
		b = Authors.objects.create(
			given_name="Ana", family_name="Silva", ORCID="https://orcid.org/0000-0002-1825-0097"
		)

		request = self._request(post_data={"apply": "1", "keep_author": str(b.pk)})
		queryset = Authors.objects.filter(pk__in=[a.pk, b.pk])
		response = self.admin.merge_selected_authors(request, queryset)

		self.assertIsNone(response)
		self.assertFalse(Authors.objects.filter(pk=a.pk).exists())
		self.assertTrue(Authors.objects.filter(pk=b.pk).exists())

	def test_merge_fills_blank_fields_from_duplicate(self):
		kept = Authors.objects.create(given_name="Ana", family_name="Silva", ORCID="0000-0002-1825-0097", country="")
		duplicate = Authors.objects.create(
			given_name="A.",
			family_name="Silva",
			ORCID="https://orcid.org/0000-0002-1825-0097",
			country="BR",
		)

		request = self._request(post_data={"apply": "1", "keep_author": str(kept.pk)})
		queryset = Authors.objects.filter(pk__in=[kept.pk, duplicate.pk])
		self.admin.merge_selected_authors(request, queryset)

		kept.refresh_from_db()
		self.assertEqual(kept.country, "BR")
