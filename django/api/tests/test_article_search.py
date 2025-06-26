from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from gregory.models import Articles
from django.utils import timezone

class ArticleSearchViewTests(TestCase):
    """Test cases for the article search endpoint"""
    
    def setUp(self):
        # Create test data
        self.article1 = Articles.objects.create(
            title="COVID-19 Research Findings",
            summary="Recent discoveries about coronavirus treatments.",
            link="https://example.com/article1",
            published_date=timezone.now()
        )
        
        self.article2 = Articles.objects.create(
            title="Multiple Sclerosis Treatment Advances",
            summary="New research on MS medications shows promise.",
            link="https://example.com/article2",
            published_date=timezone.now()
        )
        
        self.article3 = Articles.objects.create(
            title="Cancer Research Update 2025",
            summary="Breakthrough in COVID-19 related cancer research.",
            link="https://example.com/article3",
            published_date=timezone.now()
        )
        
        self.client = APIClient()
        
    def test_search_by_title(self):
        """Test searching articles by title"""
        url = reverse('article-search') + '?title=COVID'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['title'], self.article1.title)
    
    def test_search_by_summary(self):
        """Test searching articles by summary/abstract"""
        url = reverse('article-search') + '?summary=coronavirus'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['title'], self.article1.title)
    
    def test_search_combined(self):
        """Test searching in both title and summary"""
        url = reverse('article-search') + '?search=COVID'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 2)  # Should find both article1 and article3
        
        # Check that we got the expected articles
        article_titles = [article['title'] for article in response.data['results']]
        self.assertIn(self.article1.title, article_titles)
        self.assertIn(self.article3.title, article_titles)
