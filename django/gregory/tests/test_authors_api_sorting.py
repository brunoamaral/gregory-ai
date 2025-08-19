import json
from datetime import datetime, date
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.auth.models import User
from gregory.models import Authors, Articles, Team, Subject
from organizations.models import Organization
from django.db.models import Count, Q


class AuthorsAPISortingTestCase(TestCase):
	"""
	Test case to ensure Authors API sorting works correctly, especially for article_count sorting.
	
	This test prevents regression of the bug where sorting by article_count was not working
	properly due to DRF OrderingFilter interference and two-phase filtering issues.
	"""
	
	def setUp(self):
		"""Set up test data"""
		self.client = APIClient()
		
		# Create test organization
		self.organization = Organization.objects.create(
			name="Test Organization"
		)
		
		# Create test teams
		self.team1 = Team.objects.create(
			name="Test Team 1",
			slug="test-team-1",
			organization=self.organization
		)
		
		# Create test subjects
		self.subject1 = Subject.objects.create(
			subject_name="Multiple Sclerosis",
			subject_slug="multiple-sclerosis",
			team=self.team1
		)
		
		# Create test authors with different article counts
		self.author_high = Authors.objects.create(
			given_name="High",
			family_name="ArticleCount",
			full_name="High ArticleCount"
		)
		
		self.author_medium = Authors.objects.create(
			given_name="Medium", 
			family_name="ArticleCount",
			full_name="Medium ArticleCount"
		)
		
		self.author_low = Authors.objects.create(
			given_name="Low",
			family_name="ArticleCount", 
			full_name="Low ArticleCount"
		)
		
		# Create test articles with publication dates in current year
		current_year = datetime.now().year
		
		# High author gets 10 articles
		for i in range(10):
			article = Articles.objects.create(
				title=f"High Author Article {i+1}",
				discovery_date=date(current_year, 1, i+1),
				published_date=date(current_year, 1, i+1),
				article_id=10000 + i
			)
			article.authors.add(self.author_high)
			article.teams.add(self.team1)
			article.subjects.add(self.subject1)
		
		# Medium author gets 5 articles
		for i in range(5):
			article = Articles.objects.create(
				title=f"Medium Author Article {i+1}",
				discovery_date=date(current_year, 2, i+1),
				published_date=date(current_year, 2, i+1),
				article_id=20000 + i
			)
			article.authors.add(self.author_medium)
			article.teams.add(self.team1)
			article.subjects.add(self.subject1)
		
		# Low author gets 2 articles
		for i in range(2):
			article = Articles.objects.create(
				title=f"Low Author Article {i+1}",
				discovery_date=date(current_year, 3, i+1),
				published_date=date(current_year, 3, i+1),
				article_id=30000 + i
			)
			article.authors.add(self.author_low)
			article.teams.add(self.team1)
			article.subjects.add(self.subject1)
	
	def test_article_count_sorting_descending(self):
		"""Test that sorting by article_count in descending order works correctly"""
		url = reverse('authors-list')
		params = {
			'team_id': self.team1.id,
			'subject_id': self.subject1.id,
			'sort_by': 'article_count',
			'order': 'desc',
			'timeframe': 'year',
			'format': 'json'
		}
		
		response = self.client.get(url, params)
		
		# Check response status
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		
		# Parse response data
		data = response.json()
		results = data['results']
		
		# Should have exactly 3 authors
		self.assertEqual(len(results), 3)
		
		# Verify correct order (descending by article count)
		self.assertEqual(results[0]['family_name'], 'ArticleCount')
		self.assertEqual(results[0]['given_name'], 'High')
		self.assertEqual(results[0]['articles_count'], 10)
		
		self.assertEqual(results[1]['family_name'], 'ArticleCount')
		self.assertEqual(results[1]['given_name'], 'Medium')
		self.assertEqual(results[1]['articles_count'], 5)
		
		self.assertEqual(results[2]['family_name'], 'ArticleCount')
		self.assertEqual(results[2]['given_name'], 'Low')
		self.assertEqual(results[2]['articles_count'], 2)
		
		# Verify that article counts are actually in descending order
		article_counts = [author['articles_count'] for author in results]
		self.assertEqual(article_counts, sorted(article_counts, reverse=True))
	
	def test_article_count_sorting_ascending(self):
		"""Test that sorting by article_count in ascending order works correctly"""
		url = reverse('authors-list')
		params = {
			'team_id': self.team1.id,
			'subject_id': self.subject1.id,
			'sort_by': 'article_count',
			'order': 'asc',
			'timeframe': 'year',
			'format': 'json'
		}
		
		response = self.client.get(url, params)
		
		# Check response status
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		
		# Parse response data
		data = response.json()
		results = data['results']
		
		# Should have exactly 3 authors
		self.assertEqual(len(results), 3)
		
		# Verify correct order (ascending by article count)
		self.assertEqual(results[0]['family_name'], 'ArticleCount')
		self.assertEqual(results[0]['given_name'], 'Low')
		self.assertEqual(results[0]['articles_count'], 2)
		
		self.assertEqual(results[1]['family_name'], 'ArticleCount')
		self.assertEqual(results[1]['given_name'], 'Medium')
		self.assertEqual(results[1]['articles_count'], 5)
		
		self.assertEqual(results[2]['family_name'], 'ArticleCount')
		self.assertEqual(results[2]['given_name'], 'High')
		self.assertEqual(results[2]['articles_count'], 10)
		
		# Verify that article counts are actually in ascending order
		article_counts = [author['articles_count'] for author in results]
		self.assertEqual(article_counts, sorted(article_counts))
	
	def test_article_count_annotation_matches_actual_count(self):
		"""Test that the article_count annotation matches the actual database count"""
		url = reverse('authors-list')
		params = {
			'team_id': self.team1.id,
			'subject_id': self.subject1.id,
			'sort_by': 'article_count',
			'order': 'desc',
			'timeframe': 'year',
			'format': 'json'
		}
		
		response = self.client.get(url, params)
		data = response.json()
		results = data['results']
		
		# Verify each author's article count matches the database
		for result in results:
			author_id = result['author_id']
			api_count = result['articles_count']
			
			# Count articles directly from database with same filters
			current_year = datetime.now().year
			db_count = Articles.objects.filter(
				authors__author_id=author_id,
				teams__id=self.team1.id,
				subjects__id=self.subject1.id,
				published_date__gte=date(current_year, 1, 1)
			).distinct().count()
			
			self.assertEqual(
				api_count, 
				db_count,
				f"API count ({api_count}) doesn't match DB count ({db_count}) for author {author_id}"
			)
	
	def test_sorting_with_team_filter_only(self):
		"""Test sorting by article_count with only team filter"""
		url = reverse('authors-list')
		params = {
			'team_id': self.team1.id,
			'sort_by': 'article_count',
			'order': 'desc',
			'format': 'json'
		}
		
		response = self.client.get(url, params)
		
		# Check response status
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		
		# Parse response data
		data = response.json()
		results = data['results']
		
		# Should have at least our 3 test authors
		self.assertGreaterEqual(len(results), 3)
		
		# Find our test authors in the results
		our_authors = [r for r in results if r['family_name'] == 'ArticleCount']
		self.assertEqual(len(our_authors), 3)
		
		# Verify they are in correct order
		article_counts = [author['articles_count'] for author in our_authors]
		self.assertEqual(article_counts, sorted(article_counts, reverse=True))
	
	def test_sorting_without_filters(self):
		"""Test that article_count sorting works without any filters"""
		url = reverse('authors-list')
		params = {
			'sort_by': 'article_count',
			'order': 'desc',
			'format': 'json'
		}
		
		response = self.client.get(url, params)
		
		# Check response status  
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		
		# Parse response data
		data = response.json()
		results = data['results']
		
		# Should have many authors
		self.assertGreater(len(results), 0)
		
		# Verify that results are actually sorted by article count
		article_counts = [author['articles_count'] for author in results]
		self.assertEqual(article_counts, sorted(article_counts, reverse=True))
	
	def test_api_results_match_direct_query(self):
		"""
		Test that API results match a direct database query with the same parameters.
		This is the core test that would have caught the original bug.
		"""
		# Get API results
		url = reverse('authors-list')
		params = {
			'team_id': self.team1.id,
			'subject_id': self.subject1.id,
			'sort_by': 'article_count',
			'order': 'desc',
			'timeframe': 'year',
			'format': 'json'
		}
		
		response = self.client.get(url, params)
		api_results = response.json()['results']
		
		# Get direct database query results (simulating the working query from our debug)
		current_year = datetime.now().year
		date_from = date(current_year, 1, 1)
		
		db_results = Authors.objects.annotate(
			article_count=Count(
				'articles',
				filter=Q(
					articles__teams__id=self.team1.id,
					articles__subjects__id=self.subject1.id,
					articles__published_date__gte=date_from
				),
				distinct=True
			)
		).filter(
			article_count__gt=0
		).order_by('-article_count', 'author_id')
		
		# Compare the results
		self.assertEqual(len(api_results), db_results.count())
		
		for i, (api_author, db_author) in enumerate(zip(api_results, db_results)):
			self.assertEqual(
				api_author['author_id'], 
				db_author.author_id,
				f"Author ID mismatch at position {i+1}: API={api_author['author_id']}, DB={db_author.author_id}"
			)
			self.assertEqual(
				api_author['articles_count'],
				db_author.article_count,
				f"Article count mismatch for author {db_author.author_id}: API={api_author['articles_count']}, DB={db_author.article_count}"
			)
	
	def test_edge_case_no_articles_in_timeframe(self):
		"""Test behavior when no articles exist in the specified timeframe"""
		url = reverse('authors-list')
		params = {
			'team_id': self.team1.id,
			'subject_id': self.subject1.id,
			'sort_by': 'article_count',
			'order': 'desc',
			'date_from': '2030-01-01',  # Future date with no articles
			'format': 'json'
		}
		
		response = self.client.get(url, params)
		
		# Check response status
		self.assertEqual(response.status_code, status.HTTP_200_OK)
		
		# Should return empty results
		data = response.json()
		self.assertEqual(len(data['results']), 0)
	
	def test_sorting_stability_with_equal_counts(self):
		"""Test that sorting is stable when authors have equal article counts"""
		# Create two more authors with the same article count as medium author (5 articles)
		author_equal1 = Authors.objects.create(
			given_name="Equal1",
			family_name="Count",
			full_name="Equal1 Count"
		)
		
		author_equal2 = Authors.objects.create(
			given_name="Equal2", 
			family_name="Count",
			full_name="Equal2 Count"
		)
		
		# Give them both 5 articles (same as medium author)
		current_year = datetime.now().year
		for i in range(5):
			article1 = Articles.objects.create(
				title=f"Equal1 Article {i+1}",
				discovery_date=date(current_year, 4, i+1),
				published_date=date(current_year, 4, i+1),
				article_id=40000 + i
			)
			article1.authors.add(author_equal1)
			article1.teams.add(self.team1)
			article1.subjects.add(self.subject1)
			
			article2 = Articles.objects.create(
				title=f"Equal2 Article {i+1}",
				discovery_date=date(current_year, 5, i+1),
				published_date=date(current_year, 5, i+1),
				article_id=50000 + i
			)
			article2.authors.add(author_equal2)
			article2.teams.add(self.team1)
			article2.subjects.add(self.subject1)
		
		url = reverse('authors-list')
		params = {
			'team_id': self.team1.id,
			'subject_id': self.subject1.id,
			'sort_by': 'article_count',
			'order': 'desc',
			'timeframe': 'year',
			'format': 'json'
		}
		
		response = self.client.get(url, params)
		data = response.json()
		results = data['results']
		
		# Should have 5 authors now
		self.assertEqual(len(results), 5)
		
		# First should still be high count (10)
		self.assertEqual(results[0]['articles_count'], 10)
		
		# Next three should all have 5 articles, sorted by author_id as secondary sort
		five_count_authors = [r for r in results if r['articles_count'] == 5]
		self.assertEqual(len(five_count_authors), 3)
		
		# Verify secondary sort by author_id (ascending)
		author_ids = [a['author_id'] for a in five_count_authors]
		self.assertEqual(author_ids, sorted(author_ids))
		
		# Last should be low count (2)
		self.assertEqual(results[-1]['articles_count'], 2)
