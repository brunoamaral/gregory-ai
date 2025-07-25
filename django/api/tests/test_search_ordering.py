"""
Tests for search endpoint ordering functionality.
Tests the fix for the search results ordering bug.
"""

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import datetime, timedelta
from gregory.models import Articles, Trials, Team, Subject, Sources
from rest_framework.test import APIClient
from rest_framework import status
import json


class SearchOrderingTestCase(TestCase):
    """Test case for search endpoint ordering functionality"""
    
    def setUp(self):
        """Set up test data"""
        self.client = APIClient()
        
        # Create test team and subject
        self.team = Team.objects.create(name="Test Team", description="Test team for ordering tests")
        self.subject = Subject.objects.create(name="Test Subject", team=self.team)
        
        # Create test source
        self.source = Sources.objects.create(name="Test Source", link="http://test.com")
        
        # Create articles with different discovery dates
        now = timezone.now()
        
        # Article 1 - oldest (should appear last)
        self.article1 = Articles.objects.create(
            title="Oldest Article Test Search",
            link="http://test1.com",
            summary="Test summary for oldest article",
            discovery_date=now - timedelta(days=10),
            published_date=now - timedelta(days=15)
        )
        self.article1.teams.add(self.team)
        self.article1.subjects.add(self.subject)
        self.article1.sources.add(self.source)
        
        # Article 2 - middle
        self.article2 = Articles.objects.create(
            title="Middle Article Test Search",
            link="http://test2.com", 
            summary="Test summary for middle article",
            discovery_date=now - timedelta(days=5),
            published_date=now - timedelta(days=8)
        )
        self.article2.teams.add(self.team)
        self.article2.subjects.add(self.subject)
        self.article2.sources.add(self.source)
        
        # Article 3 - newest (should appear first)
        self.article3 = Articles.objects.create(
            title="Newest Article Test Search",
            link="http://test3.com",
            summary="Test summary for newest article", 
            discovery_date=now - timedelta(days=1),
            published_date=now - timedelta(days=2)
        )
        self.article3.teams.add(self.team)
        self.article3.subjects.add(self.subject)
        self.article3.sources.add(self.source)
        
        # Create trials with different discovery dates
        self.trial1 = Trials.objects.create(
            title="Oldest Trial Test Search",
            link="http://trial1.com",
            summary="Test summary for oldest trial",
            discovery_date=now - timedelta(days=12),
            published_date=now - timedelta(days=16)
        )
        self.trial1.teams.add(self.team)
        self.trial1.subjects.add(self.subject)
        self.trial1.sources.add(self.source)
        
        self.trial2 = Trials.objects.create(
            title="Middle Trial Test Search", 
            link="http://trial2.com",
            summary="Test summary for middle trial",
            discovery_date=now - timedelta(days=6),
            published_date=now - timedelta(days=9)
        )
        self.trial2.teams.add(self.team)
        self.trial2.subjects.add(self.subject)
        self.trial2.sources.add(self.source)
        
        self.trial3 = Trials.objects.create(
            title="Newest Trial Test Search",
            link="http://trial3.com",
            summary="Test summary for newest trial",
            discovery_date=now - timedelta(days=2),
            published_date=now - timedelta(days=3)
        )
        self.trial3.teams.add(self.team)
        self.trial3.subjects.add(self.subject)
        self.trial3.sources.add(self.source)
    
    def test_article_search_default_ordering(self):
        """Test that article search orders by discovery_date (newest first) by default"""
        url = reverse('article-search')
        
        params = {
            'team_id': self.team.id,
            'subject_id': self.subject.id,
            'search': 'Test Search'
        }
        
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        results = response.data['results']
        self.assertEqual(len(results), 3)
        
        # Check that articles are ordered by discovery_date (newest first)
        self.assertEqual(results[0]['article_id'], self.article3.article_id)  # Newest
        self.assertEqual(results[1]['article_id'], self.article2.article_id)  # Middle
        self.assertEqual(results[2]['article_id'], self.article1.article_id)  # Oldest
    
    def test_trial_search_default_ordering(self):
        """Test that trial search orders by discovery_date (newest first) by default"""
        url = reverse('trial-search')
        
        params = {
            'team_id': self.team.id,
            'subject_id': self.subject.id,
            'search': 'Test Search'
        }
        
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        results = response.data['results']
        self.assertEqual(len(results), 3)
        
        # Check that trials are ordered by discovery_date (newest first)
        self.assertEqual(results[0]['trial_id'], self.trial3.trial_id)  # Newest
        self.assertEqual(results[1]['trial_id'], self.trial2.trial_id)  # Middle
        self.assertEqual(results[2]['trial_id'], self.trial1.trial_id)  # Oldest
    
    def test_article_search_custom_ordering(self):
        """Test that article search respects custom ordering parameter"""
        url = reverse('article-search')
        
        params = {
            'team_id': self.team.id,
            'subject_id': self.subject.id,
            'search': 'Test Search',
            'ordering': '-published_date'  # Order by published date instead
        }
        
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        results = response.data['results']
        self.assertEqual(len(results), 3)
        
        # Check that articles are ordered by published_date (newest first)
        # Published dates: article3 (-2 days), article2 (-8 days), article1 (-15 days)
        self.assertEqual(results[0]['article_id'], self.article3.article_id)
        self.assertEqual(results[1]['article_id'], self.article2.article_id)
        self.assertEqual(results[2]['article_id'], self.article1.article_id)
    
    def test_trial_search_custom_ordering(self):
        """Test that trial search respects custom ordering parameter"""
        url = reverse('trial-search')
        
        params = {
            'team_id': self.team.id,
            'subject_id': self.subject.id,
            'search': 'Test Search',
            'ordering': 'title'  # Order by title alphabetically
        }
        
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        results = response.data['results']
        self.assertEqual(len(results), 3)
        
        # Check that trials are ordered by title alphabetically
        # Titles: "Middle Trial...", "Newest Trial...", "Oldest Trial..."
        self.assertEqual(results[0]['trial_id'], self.trial2.trial_id)  # Middle
        self.assertEqual(results[1]['trial_id'], self.trial3.trial_id)  # Newest
        self.assertEqual(results[2]['trial_id'], self.trial1.trial_id)  # Oldest
    
    def test_article_search_ordering_with_post(self):
        """Test that POST requests also support ordering"""
        url = reverse('article-search')
        
        data = {
            'team_id': self.team.id,
            'subject_id': self.subject.id,
            'search': 'Test Search',
            'ordering': 'article_id'  # Order by ID
        }
        
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        results = response.data['results']
        self.assertEqual(len(results), 3)
        
        # Check that articles are ordered by article_id (ascending)
        article_ids = [result['article_id'] for result in results]
        self.assertEqual(article_ids, sorted(article_ids))
    
    def test_trial_search_ordering_with_post(self):
        """Test that POST requests also support ordering for trials"""
        url = reverse('trial-search')
        
        data = {
            'team_id': self.team.id,
            'subject_id': self.subject.id,
            'search': 'Test Search',
            'ordering': 'trial_id'  # Order by ID
        }
        
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        results = response.data['results']
        self.assertEqual(len(results), 3)
        
        # Check that trials are ordered by trial_id (ascending)
        trial_ids = [result['trial_id'] for result in results]
        self.assertEqual(trial_ids, sorted(trial_ids))
