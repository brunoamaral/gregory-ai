from rest_framework.test import APITestCase, APIClient
from gregory.models import Authors
from django.contrib.auth.models import User


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
