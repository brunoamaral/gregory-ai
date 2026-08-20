from unittest import mock

from rest_framework.test import APITestCase, APIClient
from gregory.models import Articles, Authors, OrganizationApiSettings, Team
from django.contrib.auth.models import User
from organizations.models import Organization

from api.pagination import CappedPageNumberPagination


class TestAuthorsPaginationCap(APITestCase):
	"""HOUSE-LOAD-SPIKE-P2-QUERY-COST.md item 1: GET /authors/ previously had no
	pagination_class (fell back to DRF's plain, unbounded PageNumberPagination).
	It is the largest table in the API with the most expensive visibility
	filter, so it gets the same offset cap as /articles/ and /trials/ — via
	CappedPageNumberPagination, NOT FlexiblePagination (see that class's
	docstring for why all_results=true must stay a no-op here).
	"""

	def setUp(self):
		self.user = User.objects.create_user(username="testuser", password="12345")
		Authors.objects.create(given_name="Jane", family_name="Doe")

		self.client = APIClient()
		self.client.force_authenticate(user=self.user)

	def test_offset_within_cap_succeeds(self):
		response = self.client.get("/authors/", {"page": 1000, "page_size": 10})
		self.assertNotEqual(response.status_code, 400)

	def test_offset_over_cap_rejected(self):
		response = self.client.get("/authors/", {"page": 1001, "page_size": 10})
		self.assertEqual(response.status_code, 400)
		self.assertIn("all_results=true", str(response.data))

	def test_all_results_is_not_a_bypass(self):
		"""Unlike /articles/ and /trials/, all_results=true must NOT bypass
		pagination on /authors/ — see HOUSE-LOAD-SPIKE-P2-QUERY-COST.md item 1
		for the cost of materialising + annotating the full authors table.
		"""
		response = self.client.get(
			"/authors/", {"page": 1001, "all_results": "true"}
		)
		self.assertEqual(response.status_code, 400)

	def test_normal_first_page_unaffected(self):
		response = self.client.get("/authors/")
		self.assertEqual(response.status_code, 200)
		self.assertIn("results", response.data)
		self.assertIn("count", response.data)


class TestAuthorsPageLastOffsetCap(APITestCase):
	"""``?page=last`` is DRF's own supported page-number value.
	CappedPageNumberPagination doesn't override get_page_number the way
	FlexiblePagination does, so DRF's base implementation resolves "last"
	via a real paginator's num_pages once one exists downstream — if the
	pre-flight offset check doesn't also resolve it (against an actual
	count, since there's no cheap way to know the true last page otherwise),
	`page=last` would either crash (no real paginator yet to resolve
	against) or silently skip the check and reach the unprotected real
	resolution, defeating the whole cap.
	"""

	def setUp(self):
		# AuthorsViewSet.get_queryset only includes authors with at least
		# one article in a visible org, so "15 authors" alone isn't enough
		# to exercise the real queryset.count() this fix depends on — each
		# author needs a visible article. Public org + an anonymous client
		# keeps visibility simple: an authenticated-but-org-less caller
		# would need ?include_public=true to see a public org's authors
		# (see docs on visible_org_ids), so this deliberately doesn't
		# authenticate.
		org = Organization.objects.create(name="Page Last Org", slug="page-last-org")
		OrganizationApiSettings.objects.filter(organization=org).update(
			make_api_public=True
		)
		team = Team.objects.create(
			organization=org, name="Page Last Team", slug="page-last-team"
		)
		for i in range(15):
			author = Authors.objects.create(given_name=f"A{i}", family_name="Last")
			article = Articles.objects.create(
				title=f"Page Last Article {i}", link=f"https://ex.com/page-last-{i}"
			)
			article.teams.add(team)
			article.authors.add(author)

		self.client = APIClient()

	def test_page_last_is_rejected_when_it_would_exceed_the_cap(self):
		# 15 authors / page_size 10 (default, not client-configurable here)
		# => last page is 2, offset 20. Lower max_offset below that so
		# 'last' must be capped rather than silently resolved.
		with mock.patch.object(CappedPageNumberPagination, "max_offset", 10):
			response = self.client.get("/authors/", {"page": "last"})
		self.assertEqual(response.status_code, 400)
		self.assertIn("all_results=true", str(response.data))

	def test_page_last_still_works_within_cap(self):
		response = self.client.get("/authors/", {"page": "last"})
		self.assertEqual(response.status_code, 200)
		self.assertIn("results", response.data)
