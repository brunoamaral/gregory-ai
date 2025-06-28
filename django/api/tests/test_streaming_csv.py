import csv
import io
from django.test import TestCase, RequestFactory
from django.urls import reverse
from rest_framework.test import force_authenticate
from api.renderers import StreamingCSVRenderer
from api.views import ArticleViewSet
from gregory.models import Articles, Team, TeamCategory, Sources
from organizations.models import Organization
from django.contrib.auth.models import User

class StreamingCSVRendererTest(TestCase):
    def setUp(self):
        # Create test data
        self.source = Sources.objects.create(name="Test Source", source_for="science paper")
        
        # Create an organization first
        self.user = User.objects.create_user(username='testuser', password='12345')
        self.organization = Organization.objects.create(name="Test Organization", slug="test-org")
        self.organization.add_user(self.user)
        
        # Now create a team that belongs to the organization
        self.team = Team.objects.create(
            organization=self.organization,
            name="Test Team",
            slug="test-team"
        )
        
        self.category = TeamCategory.objects.create(
            team=self.team, 
            category_name="Test Category",
            category_slug="test-category"
        )
        
        # Create test articles
        for i in range(10):
            article = Articles.objects.create(
                title=f"Test Article {i}",
                summary=f"Test summary {i}",
                source=self.source,
                kind="article"
            )
            article.teams.add(self.team)
            article.team_categories.add(self.category)
        
        # Set up request factory
        self.factory = RequestFactory()
    
    def test_default_csv_response_is_streaming(self):
        """Test that the default CSV response is now streaming"""
        # Create a request with format=csv
        url = reverse('article-list') + '?format=csv'
        request = self.factory.get(url)
        force_authenticate(request, user=self.user)
        
        # Use the viewset directly
        view = ArticleViewSet.as_view({'get': 'list'})
        response = view(request)
        
        # Test response properties
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertTrue('attachment; filename=' in response['Content-Disposition'])
        
        # Verify the response is streaming
        self.assertTrue(hasattr(response, 'streaming_content'))
        
        # Convert the streaming response to a string
        content = b''.join(response.streaming_content).decode('utf-8')
        
        # Parse the CSV content
        csv_reader = csv.reader(io.StringIO(content))
        rows = list(csv_reader)
        
        # Verify header row
        self.assertTrue('article_id' in rows[0])
        self.assertTrue('title' in rows[0])
        
        # Verify data rows
        self.assertEqual(len(rows), 11)  # Header + 10 articles
    
    def test_streaming_csv_with_filtering(self):
        """Test that the streaming CSV renderer works with filtering"""
        # Create a request with a filter and CSV format
        url = reverse('article-list') + '?format=csv&search=Article 5'
        request = self.factory.get(url)
        force_authenticate(request, user=self.user)
        
        # Use the viewset directly
        view = ArticleViewSet.as_view({'get': 'list'})
        response = view(request)
        
        # Test response properties
        self.assertEqual(response.status_code, 200)
        
        # Convert the streaming response to a string
        content = b''.join(response.streaming_content).decode('utf-8')
        
        # Parse the CSV content
        csv_reader = csv.reader(io.StringIO(content))
        rows = list(csv_reader)
        
        # Verify we have the header plus only the filtered article
        self.assertEqual(len(rows), 2)  # Header + 1 filtered article
        
        # Verify the title contains 'Article 5'
        title_index = rows[0].index('title')
        self.assertEqual(rows[1][title_index], '"Test Article 5"')
