from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from gregory.models import Articles, Authors, Team, Subject, Organization
from django.utils import timezone

class ArticleAuthorFilterTests(TestCase):
    """Test cases for filtering articles by author_id parameter"""
    
    def setUp(self):
        # Create test organization, team and subject
        self.organization = Organization.objects.create(name="Test Organization")
        self.team = Team.objects.create(name="Test Team", organization=self.organization)
        self.subject = Subject.objects.create(
            subject_name="Test Subject",
            subject_slug="test-subject",
            team=self.team
        )
        
        # Create test authors
        self.author1 = Authors.objects.create(
            family_name="Smith",
            given_name="John",
            full_name="John Smith"
        )
        self.author2 = Authors.objects.create(
            family_name="Doe",
            given_name="Jane",
            full_name="Jane Doe"
        )
        
        # Create test articles
        self.article1 = Articles.objects.create(
            title="Article by John Smith",
            summary="Research findings by John Smith.",
            link="https://example.com/article1",
            published_date=timezone.now()
        )
        self.article1.teams.add(self.team)
        self.article1.subjects.add(self.subject)
        self.article1.authors.add(self.author1)
        
        self.article2 = Articles.objects.create(
            title="Article by Jane Doe",
            summary="Research findings by Jane Doe.",
            link="https://example.com/article2",
            published_date=timezone.now()
        )
        self.article2.teams.add(self.team)
        self.article2.subjects.add(self.subject)
        self.article2.authors.add(self.author2)
        
        self.article3 = Articles.objects.create(
            title="Collaborative Article",
            summary="Research by both authors.",
            link="https://example.com/article3",
            published_date=timezone.now()
        )
        self.article3.teams.add(self.team)
        self.article3.subjects.add(self.subject)
        self.article3.authors.add(self.author1, self.author2)
        
        self.client = APIClient()
    
    def test_filter_articles_by_author_id(self):
        """Test filtering articles by author_id parameter"""
        # Test filtering by author1
        response = self.client.get(f'/articles/?author_id={self.author1.author_id}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        results = response.data['results']
        self.assertEqual(len(results), 2)  # article1 and article3
        
        article_ids = [article['article_id'] for article in results]
        self.assertIn(self.article1.article_id, article_ids)
        self.assertIn(self.article3.article_id, article_ids)
        self.assertNotIn(self.article2.article_id, article_ids)
    
    def test_filter_articles_by_author_id_single_result(self):
        """Test filtering articles by author_id with single result"""
        # Test filtering by author2 (should return article2 and article3)
        response = self.client.get(f'/articles/?author_id={self.author2.author_id}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        results = response.data['results']
        self.assertEqual(len(results), 2)  # article2 and article3
        
        article_ids = [article['article_id'] for article in results]
        self.assertIn(self.article2.article_id, article_ids)
        self.assertIn(self.article3.article_id, article_ids)
        self.assertNotIn(self.article1.article_id, article_ids)
    
    def test_filter_articles_by_nonexistent_author_id(self):
        """Test filtering articles by non-existent author_id"""
        response = self.client.get('/articles/?author_id=99999')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        results = response.data['results']
        self.assertEqual(len(results), 0)
    
    def test_combine_author_filter_with_other_filters(self):
        """Test combining author_id filter with other parameters"""
        # Test combining author_id with team_id filter
        response = self.client.get(f'/articles/?author_id={self.author1.author_id}&team_id={self.team.id}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        results = response.data['results']
        self.assertEqual(len(results), 2)  # article1 and article3
    
    def test_author_filter_backwards_compatibility(self):
        """Test that the new author_id filter replaces the old /articles/author/{id}/ endpoint functionality"""
        # This test documents that the new filter should replace old endpoint:
        # OLD: GET /articles/author/380002/?format=json&page=1
        # NEW: GET /articles/?author_id=380002&format=json&page=1
        
        response = self.client.get(f'/articles/?author_id={self.author1.author_id}&format=json&page=1')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Verify the response structure matches what's expected
        self.assertIn('results', response.data)
        self.assertIn('count', response.data)
        self.assertIn('next', response.data)
        self.assertIn('previous', response.data)
