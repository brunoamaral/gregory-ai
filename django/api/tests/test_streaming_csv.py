import csv
import io
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from api.direct_streaming import DirectStreamingCSVRenderer
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
                link=f"https://example.com/article-{i}",
                kind="science paper"
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
        response = self.client.get(reverse('articles-list'), {'format': 'csv'})
        
        # Test response properties
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        self.assertTrue('attachment; filename=' in response['Content-Disposition'])
        
        # Verify the CSV content
        content = response.content.decode('utf-8')
        csv_reader = csv.reader(io.StringIO(content))
        rows = list(csv_reader)
        
        # Verify header row
        self.assertTrue('article_id' in rows[0])
        self.assertTrue('title' in rows[0])
        
        # Verify data rows
        self.assertEqual(len(rows), 11)  # Header + 10 articles
    
    def test_streaming_csv_with_filtering(self):
        """Test that the streaming CSV renderer works with filtering"""
        # Make a request with a filter and CSV format
        response = self.client.get(
            reverse('articles-list'),
            {'format': 'csv', 'search': 'Article 5'}
        )
        
        # Test response properties
        self.assertEqual(response.status_code, 200)
        
        # Verify the CSV content
        content = response.content.decode('utf-8')
        csv_reader = csv.reader(io.StringIO(content))
        rows = list(csv_reader)
        
        # Verify we have the header plus only the filtered article
        self.assertEqual(len(rows), 2)  # Header + 1 filtered article
        
        # Verify the title contains 'Article 5'
        title_index = rows[0].index('title')
        self.assertTrue('Test Article 5' in rows[1][title_index])
