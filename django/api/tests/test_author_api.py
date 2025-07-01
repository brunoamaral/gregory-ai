from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status
from django.db.models import Count
from gregory.models import Authors, Articles, Team, Subject, TeamCategory, Sources
from organizations.models import Organization
from django_countries.fields import Country
import json


class AuthorAPITest(TestCase):
    """Test cases for Authors API endpoints"""
    
    def setUp(self):
        """Set up test data"""
        # Create test organization and team
        self.organization = Organization.objects.create(name="Test Org")
        self.team = Team.objects.create(
            organization=self.organization,
            name="Test Team",
            slug="test-team"
        )
        
        # Create test subject
        self.subject = Subject.objects.create(
            subject_name="Test Subject",
            subject_slug="test-subject",
            team=self.team
        )
        
        # Create test category
        self.category = TeamCategory.objects.create(
            team=self.team,
            category_name="Test Category",
            category_slug="test-category"
        )
        self.category.subjects.add(self.subject)
        
        # Create test source
        self.source = Sources.objects.create(
            name="Test Source",
            team=self.team,
            subject=self.subject
        )
        
        # Create test authors
        self.author1 = Authors.objects.create(
            given_name="Jane",
            family_name="Smith",
            ORCID="0000-0002-3456-7890",
            country=Country('US')
        )
        
        self.author2 = Authors.objects.create(
            given_name="John",
            family_name="Doe",
            ORCID="0000-0001-2345-6789"
        )
        
        # Create test articles
        self.article1 = Articles.objects.create(
            title="Test Article 1",
            link="http://example.com/article1",
            summary="Test summary 1"
        )
        self.article1.authors.add(self.author1)
        self.article1.teams.add(self.team)
        self.article1.subjects.add(self.subject)
        self.article1.sources.add(self.source)
        self.article1.team_categories.add(self.category)
        
        self.article2 = Articles.objects.create(
            title="Test Article 2",
            link="http://example.com/article2",
            summary="Test summary 2"
        )
        self.article2.authors.add(self.author1)
        self.article2.teams.add(self.team)
        self.article2.subjects.add(self.subject)
        self.article2.sources.add(self.source)
        self.article2.team_categories.add(self.category)
        
        # One article for author2
        self.article3 = Articles.objects.create(
            title="Test Article 3",
            link="http://example.com/article3",
            summary="Test summary 3"
        )
        self.article3.authors.add(self.author2)
        self.article3.teams.add(self.team)
        self.article3.subjects.add(self.subject)
        self.article3.sources.add(self.source)
        
        self.client = APIClient()
        
    def test_authors_list_endpoint(self):
        """Test the authors list endpoint"""
        response = self.client.get('/authors/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)
        self.assertIn('count', response.data)
        self.assertEqual(response.data['count'], 2)
        
    def test_author_detail_endpoint(self):
        """Test the author detail endpoint"""
        response = self.client.get(f'/authors/{self.author1.author_id}/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('full_name', response.data)
        self.assertEqual(response.data['full_name'], "Jane Smith")
        self.assertEqual(response.data['author_id'], self.author1.author_id)
        
    def test_authors_sorting_by_article_count(self):
        """Test sorting authors by article count"""
        response = self.client.get('/authors/?sort_by=article_count&order=desc')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results']
        
        # author1 should come first (2 articles vs 1)
        self.assertEqual(results[0]['author_id'], self.author1.author_id)
        self.assertEqual(results[0]['articles_count'], 2)
        self.assertEqual(results[1]['author_id'], self.author2.author_id)
        self.assertEqual(results[1]['articles_count'], 1)
        
    def test_authors_filtering_validation_subject_without_team(self):
        """Test that filtering by subject_id without team_id returns empty results"""
        response = self.client.get(f'/authors/?subject_id={self.subject.id}&sort_by=article_count')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)
        self.assertEqual(len(response.data['results']), 0)
        
    def test_authors_filtering_validation_category_without_team(self):
        """Test that filtering by category_slug without team_id returns empty results"""
        response = self.client.get(f'/authors/?category_slug={self.category.category_slug}&sort_by=article_count')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)
        self.assertEqual(len(response.data['results']), 0)
        
    def test_authors_filtering_with_valid_team_and_subject(self):
        """Test filtering by team_id and subject_id works correctly"""
        response = self.client.get(f'/authors/?team_id={self.team.id}&subject_id={self.subject.id}&sort_by=article_count')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 2)
        
        # Should be sorted by article count (desc)
        results = response.data['results']
        self.assertEqual(results[0]['author_id'], self.author1.author_id)
        self.assertEqual(results[1]['author_id'], self.author2.author_id)
        
    def test_authors_filtering_with_valid_team_and_category(self):
        """Test filtering by team_id and category_slug works correctly"""
        response = self.client.get(f'/authors/?team_id={self.team.id}&category_slug={self.category.category_slug}&sort_by=article_count')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Only author1 has articles in this category
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['author_id'], self.author1.author_id)
        
    def test_authors_timeframe_filtering_year(self):
        """Test filtering by timeframe=year"""
        response = self.client.get('/authors/?sort_by=article_count&timeframe=year')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)
        
    def test_authors_timeframe_filtering_month(self):
        """Test filtering by timeframe=month"""
        response = self.client.get('/authors/?sort_by=article_count&timeframe=month')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)
        
    def test_authors_timeframe_filtering_week(self):
        """Test filtering by timeframe=week"""
        response = self.client.get('/authors/?sort_by=article_count&timeframe=week')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)
        
    def test_authors_custom_date_range_filtering(self):
        """Test filtering by custom date range"""
        response = self.client.get('/authors/?sort_by=article_count&date_from=2024-01-01&date_to=2024-12-31')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('results', response.data)
        
    def test_authors_action_by_team_subject_validation(self):
        """Test by_team_subject action requires both team_id and subject_id"""
        # Missing both parameters
        response = self.client.get('/authors/by_team_subject/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        
        # Missing subject_id
        response = self.client.get(f'/authors/by_team_subject/?team_id={self.team.id}')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        
        # Missing team_id
        response = self.client.get(f'/authors/by_team_subject/?subject_id={self.subject.id}')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        
        # Valid parameters
        response = self.client.get(f'/authors/by_team_subject/?team_id={self.team.id}&subject_id={self.subject.id}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
    def test_authors_action_by_team_category_validation(self):
        """Test by_team_category action requires both team_id and category_slug"""
        # Missing both parameters
        response = self.client.get('/authors/by_team_category/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        
        # Missing category_slug
        response = self.client.get(f'/authors/by_team_category/?team_id={self.team.id}')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        
        # Missing team_id
        response = self.client.get(f'/authors/by_team_category/?category_slug={self.category.category_slug}')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        
        # Valid parameters
        response = self.client.get(f'/authors/by_team_category/?team_id={self.team.id}&category_slug={self.category.category_slug}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
    def test_authors_serializer_fields(self):
        """Test that author serializer includes all expected fields"""
        response = self.client.get('/authors/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        if response.data['results']:
            author_data = response.data['results'][0]
            expected_fields = [
                'author_id', 'given_name', 'family_name', 'full_name',
                'ORCID', 'country', 'articles_count', 'articles_list'
            ]
            
            for field in expected_fields:
                self.assertIn(field, author_data, f"Field '{field}' missing from API response")
                
    def test_authors_ordering_by_different_fields(self):
        """Test ordering by different fields"""
        # Test ordering by given_name
        response = self.client.get('/authors/?sort_by=given_name&order=asc')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Test ordering by family_name
        response = self.client.get('/authors/?sort_by=family_name&order=desc')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
    def test_authors_pagination(self):
        """Test that pagination works correctly"""
        response = self.client.get('/authors/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('count', response.data)
        self.assertIn('results', response.data)
        self.assertIn('next', response.data)
        self.assertIn('previous', response.data)
        
    def test_team_id_requirement_validation(self):
        """
        Test that team_id is required when filtering by category_slug or subject_id.
        This enforces the business rule that authors must be filtered within a team context
        when using subject or category filters.
        """
        # Test 1: Basic endpoint without filters (should work)
        response = self.client.get('/authors/?sort_by=article_count')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Test 2: With team_id only (should work)
        response = self.client.get(f'/authors/?team_id={self.team.id}&sort_by=article_count')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Test 3: With subject_id but no team_id (should return empty results)
        response = self.client.get(f'/authors/?subject_id={self.subject.id}&sort_by=article_count')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0, 
                        "Should return empty results when subject_id is used without team_id")
        self.assertEqual(len(response.data['results']), 0)
        
        # Test 4: With category_slug but no team_id (should return empty results)
        response = self.client.get(f'/authors/?category_slug={self.category.category_slug}&sort_by=article_count')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0,
                        "Should return empty results when category_slug is used without team_id")
        self.assertEqual(len(response.data['results']), 0)
        
        # Test 5: With team_id and subject_id (should work and potentially return results)
        response = self.client.get(f'/authors/?team_id={self.team.id}&subject_id={self.subject.id}&sort_by=article_count')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Note: Results depend on test data, but request should be valid
        
        # Test 6: With team_id and category_slug (should work and potentially return results)
        response = self.client.get(f'/authors/?team_id={self.team.id}&category_slug={self.category.category_slug}&sort_by=article_count')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Note: Results depend on test data, but request should be valid
        
        # Test 7: Both subject_id and category_slug without team_id (should return empty)
        response = self.client.get(f'/authors/?subject_id={self.subject.id}&category_slug={self.category.category_slug}&sort_by=article_count')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0,
                        "Should return empty results when both subject_id and category_slug are used without team_id")
        self.assertEqual(len(response.data['results']), 0)
        
        # Test 8: Invalid team_id with valid subject_id (should return empty but not error)
        invalid_team_id = 99999
        response = self.client.get(f'/authors/?team_id={invalid_team_id}&subject_id={self.subject.id}&sort_by=article_count')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)
        
        # Test 9: Valid team_id with invalid subject_id (should return empty but not error)
        invalid_subject_id = 99999
        response = self.client.get(f'/authors/?team_id={self.team.id}&subject_id={invalid_subject_id}&sort_by=article_count')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)

    def test_team_id_requirement_with_timeframe_filters(self):
        """
        Test that team_id requirement is enforced even when using timeframe filters
        """
        from datetime import datetime, timedelta
        
        # Date filters
        date_from = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        date_to = datetime.now().strftime('%Y-%m-%d')
        
        # Test with subject_id and date filters but no team_id (should return empty)
        response = self.client.get(f'/authors/?subject_id={self.subject.id}&date_from={date_from}&date_to={date_to}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0,
                        "Should return empty results when subject_id is used with date filters but without team_id")
        
        # Test with category_slug and timeframe but no team_id (should return empty)
        response = self.client.get(f'/authors/?category_slug={self.category.category_slug}&timeframe=last_month')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0,
                        "Should return empty results when category_slug is used with timeframe but without team_id")
        
        # Test with all filters including team_id (should work)
        response = self.client.get(f'/authors/?team_id={self.team.id}&subject_id={self.subject.id}&timeframe=last_month&sort_by=article_count')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should not error, regardless of whether there are results

    def test_validation_error_messages_clarity(self):
        """
        Test that the API provides clear behavior for team_id requirement violations.
        While we return empty results instead of errors for the main endpoint,
        the action endpoints should provide clear error messages.
        """
        # Test action endpoint validation messages
        response = self.client.get('/authors/by_team_subject/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        self.assertTrue(
            'team_id' in str(response.data['error']).lower() or 
            'subject_id' in str(response.data['error']).lower(),
            "Error message should mention required parameters"
        )
        
        response = self.client.get('/authors/by_team_category/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        self.assertTrue(
            'team_id' in str(response.data['error']).lower() or 
            'category_slug' in str(response.data['error']).lower(),
            "Error message should mention required parameters"
        )
