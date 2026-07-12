"""
Tests for the optimized category queries to ensure they perform well
and return correct results.
"""

from django.test import TestCase
from django.test.utils import override_settings
from django.db import connection
from rest_framework.test import APIClient
from django.contrib.sites.models import Site
from gregory.models import (
	TeamCategory,
	Team,
	Articles,
	Trials,
	Authors,
	Subject,
	OrganizationApiSettings,
)
from organizations.models import Organization
import time


class CategoryOptimizationTestCase(TestCase):
	"""Test cases for the optimized category queries"""

	def setUp(self):
		"""Set up test data"""
		self.client = APIClient()

		# Create organization first (required for Team)
		Site.objects.clear_cache()  # ensure consistent query count regardless of run order
		self.organization = Organization.objects.create(
			name="Test Organization", slug="test-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.organization).update(
			make_api_public=True
		)

		# Create test team
		self.team = Team.objects.create(
			name="Test Team", organization=self.organization, slug="test-team"
		)

		# Create test subject
		self.subject = Subject.objects.create(subject_name="Test Subject")

		# Create test category
		self.category = TeamCategory.objects.create(
			team=self.team, category_name="Test Category", category_slug="test-category"
		)
		self.category.subjects.add(self.subject)

		# Create test authors
		self.author1 = Authors.objects.create(
			given_name="John", family_name="Doe", full_name="John Doe"
		)
		self.author2 = Authors.objects.create(
			given_name="Jane", family_name="Smith", full_name="Jane Smith"
		)

		# Create test articles
		for i in range(5):
			article = Articles.objects.create(
				title=f"Test Article {i}",
				link=f"http://example.com/article/{i}",
				summary=f"Summary for article {i}",
			)
			article.team_categories.add(self.category)
			article.authors.add(self.author1 if i < 3 else self.author2)

		# Create test trials
		for i in range(3):
			trial = Trials.objects.create(
				title=f"Test Trial {i}",
				link=f"http://example.com/trial/{i}",
				summary=f"Summary for trial {i}",
			)
			trial.team_categories.add(self.category)

	def test_category_list_performance(self):
		"""Test that category listing is fast and returns correct counts"""
		start_time = time.time()

		response = self.client.get(f"/categories/?team_id={self.team.id}")

		end_time = time.time()
		query_time = end_time - start_time

		self.assertEqual(response.status_code, 200)
		self.assertLess(query_time, 1.0)  # Should complete in under 1 second

		data = response.json()
		self.assertEqual(len(data["results"]), 1)

		category_data = data["results"][0]
		self.assertEqual(category_data["article_count_total"], 5)
		self.assertEqual(category_data["trials_count_total"], 3)
		self.assertEqual(category_data["authors_count"], 2)

	def test_category_authors_endpoint(self):
		"""Test the category authors endpoint performance"""
		start_time = time.time()

		response = self.client.get(f"/categories/{self.category.id}/authors/")

		end_time = time.time()
		query_time = end_time - start_time

		self.assertEqual(response.status_code, 200)
		self.assertLess(query_time, 1.0)  # Should complete in under 1 second

		data = response.json()
		self.assertEqual(len(data["results"]), 2)

		# Check that authors are sorted by article count
		author1_data = next(a for a in data["results"] if a["full_name"] == "John Doe")
		author2_data = next(
			a for a in data["results"] if a["full_name"] == "Jane Smith"
		)

		self.assertEqual(author1_data["articles_count"], 3)
		self.assertEqual(author2_data["articles_count"], 2)

	def test_query_count_optimization(self):
		"""Test that we're not generating excessive database queries"""
		with self.assertNumQueries(
			7
		):  # site + org visibility + count + select (with count annotations) + subjects prefetch + authors count + authors select
			response = self.client.get("/categories/")

		self.assertEqual(response.status_code, 200)

	def test_complex_category_filtering(self):
		"""Test complex filtering scenarios that previously caused hanging"""
		# Create additional test data for more complex scenario
		team2 = Team.objects.create(
			name="Team 2", organization=self.organization, slug="team-2"
		)
		subject2 = Subject.objects.create(subject_name="Subject 2")

		category2 = TeamCategory.objects.create(
			team=team2, category_name="Category 2", category_slug="category-2"
		)
		category2.subjects.add(subject2)

		# Test multiple filters
		start_time = time.time()

		response = self.client.get(
			f"/categories/?team_id={self.team.id}&subject_id={self.subject.id}"
		)

		end_time = time.time()
		query_time = end_time - start_time

		self.assertEqual(response.status_code, 200)
		self.assertLess(query_time, 1.0)  # Should complete quickly

		data = response.json()
		self.assertEqual(len(data["results"]), 1)
		self.assertEqual(data["results"][0]["id"], self.category.id)

	@override_settings(DEBUG=True)  # To capture SQL queries
	def test_query_count_independent_of_article_and_trial_volume(self):
		"""Counts come from Count(..., distinct=True) annotations over a single
		bounded query, not from prefetching every article/trial row -- so the
		query count for a category listing must not grow with how many
		articles/trials the category has. (A LEFT OUTER JOIN + GROUP BY here is
		expected and fine: post-#747/#749 it costs one aggregation query over a
		small page of categories, never a full materialisation of their rows.)"""
		# Warm the Site cache first so it doesn't add an extra query to only
		# the baseline call.
		self.client.get(f"/categories/?team_id={self.team.id}")

		connection.queries.clear()
		response = self.client.get(f"/categories/?team_id={self.team.id}")
		self.assertEqual(response.status_code, 200)
		baseline_query_count = len(connection.queries)

		for i in range(50):
			article = Articles.objects.create(
				title=f"Bulk Article {i}",
				link=f"http://example.com/bulk-article/{i}",
				summary=f"Summary {i}",
			)
			article.team_categories.add(self.category)

		connection.queries.clear()
		response = self.client.get(f"/categories/?team_id={self.team.id}")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()["results"][0]["article_count_total"], 55)
		self.assertEqual(len(connection.queries), baseline_query_count)

	def test_prefetch_efficiency(self):
		"""Test that our prefetch_related optimizations work correctly"""
		# Test with include_authors=false (should be very efficient)
		with self.assertNumQueries(
			6
		):  # Basic query (with count annotations) + subjects prefetch + authors count query + visibility queries
			response = self.client.get(
				f"/categories/?team_id={self.team.id}&include_authors=false"
			)
			self.assertEqual(response.status_code, 200)

		# Test with include_authors=true (should still be reasonable; site is cached from first request)
		with self.assertNumQueries(
			6
		):  # org + count + select (with count annotations) + subjects prefetch + authors count + authors select
			response = self.client.get(
				f"/categories/?team_id={self.team.id}&include_authors=true"
			)
			self.assertEqual(response.status_code, 200)


class IndexEfficiencyTestCase(TestCase):
	"""Verify that the expected database indexes exist on category-related tables."""

	def _index_names(self, table):
		with connection.cursor() as cursor:
			cursor.execute(
				"SELECT indexname FROM pg_indexes WHERE tablename = %s",
				[table],
			)
			return {row[0] for row in cursor.fetchall()}

	def test_explain_query_uses_indexes(self):
		"""Indexes needed for team_id filtering and category→article joins exist."""
		tc_indexes = self._index_names("team_categories")
		atc_indexes = self._index_names("articles_team_categories")

		# team_categories must be indexable by team_id
		self.assertIn("idx_team_categories_team_id", tc_indexes)
		# composite (team_id, id) covering index for category list queries
		self.assertIn("idx_team_categories_team_subject", tc_indexes)
		# articles_team_categories must be indexable by teamcategory_id
		self.assertIn("idx_articles_team_categories_category_id", atc_indexes)

	def test_covering_index_usage(self):
		"""Covering index on articles(article_id, title, published_date, discovery_date) exists."""
		article_indexes = self._index_names("articles")
		self.assertIn("idx_articles_covering", article_indexes)
