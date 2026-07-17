import csv
import io
from django.test import TestCase
from django.urls import reverse
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory
from gregory.models import Articles, OrganizationApiSettings, Team, TeamCategory, Sources
from organizations.models import Organization
from django.contrib.auth.models import User
from api.direct_streaming import DirectStreamingCSVRenderer
from api.serializers import ArticleSerializer


class StreamingCSVRendererTest(TestCase):
	def setUp(self):
		# Create test data
		self.source = Sources.objects.create(
			name="Test Source", source_for="science paper"
		)

		# Create an organization first
		self.user = User.objects.create_user(username="testuser", password="12345")
		self.organization = Organization.objects.create(
			name="Test Organization", slug="test-org"
		)
		self.organization.add_user(self.user)

		# Now create a team that belongs to the organization
		self.team = Team.objects.create(
			organization=self.organization, name="Test Team", slug="test-team"
		)

		self.category = TeamCategory.objects.create(
			team=self.team, category_name="Test Category", category_slug="test-category"
		)

		# Create test articles
		for i in range(10):
			article = Articles.objects.create(
				title=f"Test Article {i}",
				summary=f"Test summary {i}",
				link=f"https://example.com/article-{i}",
				kind="science paper",
			)
			# Add source after creation (ManyToMany relationship)
			article.sources.add(self.source)
			article.teams.add(self.team)
			article.team_categories.add(self.category)

		# Set up client
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)

	def test_default_csv_response_is_streaming(self):
		"""Test that the default CSV response is now streaming"""
		# Make a request with format=csv
		response = self.client.get(reverse("articles-list"), {"format": "csv"})

		# Test response properties
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
		self.assertTrue("attachment; filename=" in response["Content-Disposition"])

		# Verify the CSV content - consume streaming_content
		# streaming_content is a generator, so we need to iterate through it
		content_bytes = b"".join(response.streaming_content)
		content = content_bytes.decode("utf-8")
		csv_reader = csv.reader(io.StringIO(content))
		rows = list(csv_reader)

		# Verify header row
		self.assertTrue("article_id" in rows[0])
		self.assertTrue("title" in rows[0])

		# Verify data rows
		self.assertEqual(len(rows), 11)  # Header + 10 articles

	def test_streaming_csv_with_filtering(self):
		"""Test that the streaming CSV renderer works with filtering"""
		# Make a request with a filter and CSV format
		response = self.client.get(
			reverse("articles-list"), {"format": "csv", "search": "Article 5"}
		)

		# Test response properties
		self.assertEqual(response.status_code, 200)

		# Verify the CSV content - consume streaming_content
		# streaming_content is a generator, so we need to iterate through it
		content_bytes = b"".join(response.streaming_content)
		content = content_bytes.decode("utf-8")
		csv_reader = csv.reader(io.StringIO(content))
		rows = list(csv_reader)

		# Verify we have the header plus only the filtered article
		self.assertEqual(len(rows), 2)  # Header + 1 filtered article

		# Verify the title contains 'Article 5'
		title_index = rows[0].index("title")
		self.assertTrue("Test Article 5" in rows[1][title_index])

	def test_streamed_output_matches_legacy_renderer(self):
		"""The batched streaming path must produce the same rows/columns as
		fully serializing the queryset and running it through the legacy
		buffered renderer (column order, quoting, JSON-encoded cells, text
		cleaning all preserved)."""
		response = self.client.get(
			reverse("articles-list"), {"format": "csv", "all_results": "true"}
		)
		streamed = b"".join(response.streaming_content).decode("utf-8")

		factory = APIRequestFactory()
		wsgi_request = factory.get("/articles/?format=csv&all_results=true")
		wsgi_request.user = self.user
		drf_request = Request(wsgi_request)
		drf_request.visible_org_ids = {self.organization.id}

		data = ArticleSerializer(
			Articles.objects.all().order_by("-discovery_date"),
			many=True,
			context={"request": drf_request},
		).data
		legacy = DirectStreamingCSVRenderer().render(data).decode("utf-8")

		streamed_rows = sorted(csv.reader(io.StringIO(streamed)))
		legacy_rows = sorted(csv.reader(io.StringIO(legacy)))
		self.assertEqual(streamed_rows, legacy_rows)

	def test_all_results_streams_in_multiple_batches(self):
		"""With chunk_size smaller than the row count, the response must be
		produced across more than one batch (header chunk + >=2 data chunks)."""
		from api.views import ArticleViewSet

		original_chunk_size = ArticleViewSet.csv_stream_chunk_size
		ArticleViewSet.csv_stream_chunk_size = 3
		try:
			response = self.client.get(
				reverse("articles-list"), {"format": "csv", "all_results": "true"}
			)
			self.assertTrue(response.streaming)
			chunks = list(response.streaming_content)
			self.assertGreater(len(chunks), 2)
		finally:
			ArticleViewSet.csv_stream_chunk_size = original_chunk_size

	def test_header_omits_per_org_fields_with_no_org_context(self):
		"""Anonymous callers with no team_id and no public org see neither
		takeaways nor summary_plain_english in the header, and every data
		row lines up with the header length."""
		anon_client = APIClient()
		response = anon_client.get(
			reverse("articles-list"), {"format": "csv", "all_results": "true"}
		)
		content = b"".join(response.streaming_content).decode("utf-8")
		rows = list(csv.reader(io.StringIO(content)))
		header = rows[0]

		self.assertNotIn("takeaways", header)
		self.assertNotIn("summary_plain_english", header)
		for row in rows[1:]:
			self.assertEqual(len(row), len(header))

	def test_header_includes_per_org_fields_with_public_team_id(self):
		"""Per spec §6: an anonymous caller with ?team_id on a public org
		gets the org context, so both per-org columns appear."""
		OrganizationApiSettings.objects.update_or_create(
			organization=self.organization, defaults={"make_api_public": True}
		)
		anon_client = APIClient()
		response = anon_client.get(
			reverse("articles-list"),
			{"format": "csv", "all_results": "true", "team_id": self.team.id},
		)
		content = b"".join(response.streaming_content).decode("utf-8")
		rows = list(csv.reader(io.StringIO(content)))
		header = rows[0]

		self.assertIn("takeaways", header)
		self.assertIn("summary_plain_english", header)


class StreamingCSVQueryBoundsTest(TestCase):
	"""Bounded-batch prefetching: query count should grow with the number of
	batches, not with the number of rows."""

	def setUp(self):
		self.source = Sources.objects.create(
			name="Test Source", source_for="science paper"
		)
		self.user = User.objects.create_user(username="qbtest", password="12345")
		self.organization = Organization.objects.create(
			name="QB Organization", slug="qb-org"
		)
		self.organization.add_user(self.user)
		self.team = Team.objects.create(
			organization=self.organization, name="QB Team", slug="qb-team"
		)
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)

	def _create_articles(self, count):
		Articles.objects.all().delete()
		for i in range(count):
			article = Articles.objects.create(
				title=f"QB Article {i}",
				summary=f"QB summary {i}",
				link=f"https://example.com/qb-article-{i}",
				kind="science paper",
			)
			article.sources.add(self.source)
			article.teams.add(self.team)

	def test_query_count_bounded_by_batches_not_rows(self):
		from django.db import connection
		from django.test.utils import CaptureQueriesContext

		from api.views import ArticleViewSet

		original_chunk_size = ArticleViewSet.csv_stream_chunk_size
		ArticleViewSet.csv_stream_chunk_size = 3
		try:
			self._create_articles(10)
			with CaptureQueriesContext(connection) as small_ctx:
				response = self.client.get(
					reverse("articles-list"), {"format": "csv", "all_results": "true"}
				)
				list(response.streaming_content)
			small_query_count = len(small_ctx.captured_queries)

			self._create_articles(20)
			with CaptureQueriesContext(connection) as large_ctx:
				response = self.client.get(
					reverse("articles-list"), {"format": "csv", "all_results": "true"}
				)
				list(response.streaming_content)
			large_query_count = len(large_ctx.captured_queries)

			# 10 rows / chunk_size 3 -> 4 batches; 20 rows -> 7 batches, i.e. 3
			# extra batches. ArticleSerializer prefetches ~8 relations, so each
			# extra batch costs roughly one query per prefetch (~8-10 queries).
			# The bound here (15/batch) is generous but still an order of
			# magnitude below what per-row (unbatched) prefetching across the
			# 10 extra rows would cost.
			extra_batches = 3
			self.assertLess(
				large_query_count - small_query_count, extra_batches * 15
			)
		finally:
			ArticleViewSet.csv_stream_chunk_size = original_chunk_size
