from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from gregory.models import Articles, Team, Subject, Organization
from django.utils import timezone
import json

class ArticleSearchViewTests(TestCase):
    """Test cases for the article search endpoint"""
    
    def setUp(self):
        # Create test organization, team and subject
        self.organization = Organization.objects.create(name="Test Organization")
        self.team = Team.objects.create(name="Test Team", organization=self.organization)
        self.subject = Subject.objects.create(
            subject_name="Test Subject",
            subject_slug="test-subject",
            team=self.team
        )
        
        # Create test data
        self.article1 = Articles.objects.create(
            title="COVID-19 Research Findings",
            summary="Recent discoveries about coronavirus treatments.",
            link="https://example.com/article1",
            published_date=timezone.now()
        )
        self.article1.teams.add(self.team)
        self.article1.subjects.add(self.subject)
        
        self.article2 = Articles.objects.create(
            title="Multiple Sclerosis Treatment Advances",
            summary="New research on MS medications shows promise.",
            link="https://example.com/article2",
            published_date=timezone.now()
        )
        self.article2.teams.add(self.team)
        self.article2.subjects.add(self.subject)
        
        self.article3 = Articles.objects.create(
            title="Cancer Research Update 2025",
            summary="Breakthrough in COVID-19 related cancer research.",
            link="https://example.com/article3",
            published_date=timezone.now()
        )
        self.article3.teams.add(self.team)
        self.article3.subjects.add(self.subject)
        
        self.client = APIClient()
    
    def test_missing_required_parameters(self):
        """Test that team_id and subject_id are required"""
        url = reverse('article-search')
        
        # Missing both parameters
        response = self.client.post(url, {}, format='json')
        self.assertEqual(response.status_code, 400)
        
        # Missing subject_id
        response = self.client.post(url, {'team_id': self.team.id}, format='json')
        self.assertEqual(response.status_code, 400)
        
        # Missing team_id
        response = self.client.post(url, {'subject_id': self.subject.id}, format='json')
        self.assertEqual(response.status_code, 400)
        
    def test_search_by_title(self):
        """Test searching articles by title"""
        url = reverse('article-search')
        data = {
            'team_id': self.team.id,
            'subject_id': self.subject.id,
            'title': 'COVID'
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['title'], self.article1.title)
    
    def test_search_by_summary(self):
        """Test searching articles by summary/abstract"""
        url = reverse('article-search')
        data = {
            'team_id': self.team.id,
            'subject_id': self.subject.id,
            'summary': 'coronavirus'
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['title'], self.article1.title)
    
    def test_search_combined(self):
        """Test searching in both title and summary"""
        url = reverse('article-search')
        data = {
            'team_id': self.team.id,
            'subject_id': self.subject.id,
            'search': 'COVID'
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 2)  # Should find both article1 and article3
        
        # Check that we got the expected articles
        article_titles = [article['title'] for article in response.data['results']]
        self.assertIn(self.article1.title, article_titles)
        self.assertIn(self.article3.title, article_titles)
    
    def test_invalid_team_id(self):
        """Test with invalid team ID"""
        url = reverse('article-search')
        data = {
            'team_id': 9999,  # Non-existent team ID
            'subject_id': self.subject.id
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, 404)
        
    def test_invalid_subject_id(self):
        """Test with invalid subject ID"""
        url = reverse('article-search')
        data = {
            'team_id': self.team.id,
            'subject_id': 9999  # Non-existent subject ID
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, 404)
