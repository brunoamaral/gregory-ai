import unittest
from django.test import RequestFactory, TestCase
from django.http import StreamingHttpResponse
from api.direct_streaming import DirectStreamingCSVRenderer
from rest_framework.test import APITestCase, APIClient
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.request import Request
from gregory.models import Articles, Team, Sources
from organizations.models import Organization
from django.contrib.auth.models import User

class TestCSVRenderer(APITestCase):
    """
    Test the DirectStreamingCSVRenderer to ensure it correctly handles both
    paginated and full exports. Tests are now done through the API view layer
    to ensure streaming works end-to-end.
    """
    
    def setUp(self):
        # Create test data through the API
        self.user = User.objects.create_user(username='testuser', password='12345')
        self.organization = Organization.objects.create(name="Test Organization", slug="test-org")
        self.organization.add_user(self.user)
        
        self.team = Team.objects.create(
            organization=self.organization,
            name="Test Team",
            slug="test-team"
        )
        
        self.source = Sources.objects.create(name="Test Source", source_for="science paper")
        
        # Create test articles
        for i in range(1, 21):  # Create 20 articles for pagination testing
            article = Articles.objects.create(
                title=f'Test Article {i}',
                summary=f'Summary {i}',
                link=f'https://example.com/article-{i}',
                kind='science paper'
            )
            article.sources.add(self.source)
            article.teams.add(self.team)
        
        # Set up API client
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        
    def test_paginated_csv_export(self):
        """Test that paginated CSV exports respect pagination."""
        # Make a request with format=csv and pagination
        response = self.client.get('/articles/', {'format': 'csv', 'page_size': 10})
        
        # Check that we got a StreamingHttpResponse
        self.assertIsInstance(response, StreamingHttpResponse)
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        
        # Collect the streaming content
        content = b''.join(response.streaming_content).decode('utf-8')
        
        # Count the rows (should be 10 data rows + 1 header)
        rows = content.strip().split('\n')
        self.assertEqual(len(rows), 11)  # 10 data rows + header
        
    def test_full_csv_export(self):
        """Test that CSV export returns all paginated results (respects default page_size)."""
        # Make a request with format=csv (no all_results parameter)
        # This should return the default paginated set
        response = self.client.get('/articles/', {'format': 'csv'})
        
        # Check response type and status
        self.assertIsInstance(response, StreamingHttpResponse)
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        self.assertIn('Content-Disposition', response)
        
        # Collect the streaming content
        content = b''.join(response.streaming_content).decode('utf-8')
        
        # Count the rows (should be default page_size rows + 1 header)
        # Default page size is 10, so we expect 10 data rows + 1 header = 11 total
        rows = content.strip().split('\n')
        self.assertEqual(len(rows), 11)  # 10 data rows (default page size) + header

if __name__ == '__main__':
    unittest.main()
