from rest_framework.test import APITestCase, APIClient
from gregory.models import Articles, Team, Sources
from organizations.models import Organization
from django.contrib.auth.models import User


class TestFlexiblePaginationDeepOffsetCap(APITestCase):
	"""HOUSE-LOAD-SPIKE-PLAN.md P1 item 4: FlexiblePagination.max_offset rejects
	requests that would sort/scan far past what any real client needs, since an
	unfiltered deep-offset request degrades into a full table sort regardless of
	whether any data actually exists that far out.
	"""

	def setUp(self):
		self.user = User.objects.create_user(username="testuser", password="12345")
		self.organization = Organization.objects.create(
			name="Test Organization", slug="test-org"
		)
		self.organization.add_user(self.user)

		self.team = Team.objects.create(
			organization=self.organization, name="Test Team", slug="test-team"
		)

		self.source = Sources.objects.create(
			name="Test Source", source_for="science paper"
		)

		article = Articles.objects.create(
			title="Test Article",
			summary="Summary",
			link="https://example.com/article-1",
			kind="science paper",
		)
		article.sources.add(self.source)
		article.teams.add(self.team)

		self.client = APIClient()
		self.client.force_authenticate(user=self.user)

	def test_offset_within_cap_succeeds(self):
		# page 1000 * page_size 10 = 10000, at the cap boundary (not over it).
		# Only one article exists, so DRF's own "page has no results" 404 is
		# expected here — the point is that this is NOT the cap's 400.
		response = self.client.get("/articles/", {"page": 1000, "page_size": 10})
		self.assertNotEqual(response.status_code, 400)

	def test_offset_over_cap_rejected(self):
		response = self.client.get("/articles/", {"page": 1001, "page_size": 10})
		self.assertEqual(response.status_code, 400)
		self.assertIn("all_results=true", str(response.data))

	def test_offset_over_cap_rejected_via_large_page_size(self):
		response = self.client.get("/articles/", {"page": 101, "page_size": 100})
		self.assertEqual(response.status_code, 400)

	def test_all_results_bypasses_cap(self):
		response = self.client.get(
			"/articles/", {"page": 999999, "all_results": "true"}
		)
		self.assertEqual(response.status_code, 200)

	def test_trials_endpoint_also_capped(self):
		response = self.client.get("/trials/", {"page": 1001, "page_size": 10})
		self.assertEqual(response.status_code, 400)
