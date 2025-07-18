from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from gregory.models import Authors
from api.serializers import AuthorSerializer
from django.contrib.sites.models import Site

class AuthorURLFormatTests(TestCase):
    """Test cases specifically for verifying the correct URL format in author responses"""
    
    def setUp(self):
        # Create test author
        self.author = Authors.objects.create(
            family_name="Smith",
            given_name="John",
            full_name="John Smith"
        )
        
        # Set up site for URL generation
        site = Site.objects.get_current()
        site.domain = 'api.gregory-ms.com'
        site.save()
        
        self.client = APIClient()
    
    def test_author_serializer_articles_list_url_format(self):
        """Test that AuthorSerializer generates the new URL format for articles_list"""
        serializer = AuthorSerializer(self.author)
        data = serializer.data
        
        expected_url = f"https://api.gregory-ms.com/articles/?author_id={self.author.author_id}"
        self.assertEqual(data['articles_list'], expected_url)
        
        # Verify it's not using the old format
        old_url = f"https://api.gregory-ms.com/articles/author/{self.author.author_id}"
        self.assertNotEqual(data['articles_list'], old_url)
    
    def test_authors_endpoint_response_url_format(self):
        """Test that the /authors/ endpoint returns the correct articles_list URL format"""
        response = self.client.get('/authors/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        results = response.data['results']
        self.assertGreater(len(results), 0)
        
        # Check the first author's articles_list URL
        author_data = results[0]
        self.assertIn('articles_list', author_data)
        
        # Should use the new format with query parameters
        articles_list_url = author_data['articles_list']
        self.assertIn('articles/?author_id=', articles_list_url)
        self.assertNotIn('articles/author/', articles_list_url)
    
    def test_single_author_endpoint_response_url_format(self):
        """Test that the /authors/{id}/ endpoint returns the correct articles_list URL format"""
        response = self.client.get(f'/authors/{self.author.author_id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Check the articles_list URL
        self.assertIn('articles_list', response.data)
        articles_list_url = response.data['articles_list']
        
        # Should use the new format with query parameters
        expected_url = f"https://api.gregory-ms.com/articles/?author_id={self.author.author_id}"
        self.assertEqual(articles_list_url, expected_url)
        
        # Verify it's not using the old format
        old_url = f"https://api.gregory-ms.com/articles/author/{self.author.author_id}"
        self.assertNotEqual(articles_list_url, old_url)
