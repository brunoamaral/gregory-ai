from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from gregory.models import Trials
from django.utils import timezone

class TrialSearchViewTests(TestCase):
    """Test cases for the trial search endpoint"""
    
    def setUp(self):
        # Create test data
        self.trial1 = Trials.objects.create(
            title="COVID-19 Vaccine Trial",
            summary="Testing efficacy of mRNA vaccines for coronavirus.",
            link="https://example.com/trial1",
            published_date=timezone.now(),
            recruitment_status="Recruiting"
        )
        
        self.trial2 = Trials.objects.create(
            title="Multiple Sclerosis Treatment Trial",
            summary="New MS medication phase 3 study.",
            link="https://example.com/trial2",
            published_date=timezone.now(),
            recruitment_status="Active, not recruiting"
        )
        
        self.trial3 = Trials.objects.create(
            title="Cancer Research Protocol 2025",
            summary="Breakthrough in COVID-19 related cancer treatment.",
            link="https://example.com/trial3",
            published_date=timezone.now(),
            recruitment_status="Completed"
        )
        
        self.client = APIClient()
        
    def test_search_by_title(self):
        """Test searching trials by title"""
        url = reverse('trial-search') + '?title=COVID'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['title'], self.trial1.title)
    
    def test_search_by_summary(self):
        """Test searching trials by summary/abstract"""
        url = reverse('trial-search') + '?summary=coronavirus'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['title'], self.trial1.title)
    
    def test_search_combined(self):
        """Test searching in both title and summary"""
        url = reverse('trial-search') + '?search=COVID'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 2)  # Should find both trial1 and trial3
        
        # Check that we got the expected trials
        trial_titles = [trial['title'] for trial in response.data['results']]
        self.assertIn(self.trial1.title, trial_titles)
        self.assertIn(self.trial3.title, trial_titles)
        
    def test_filter_by_status(self):
        """Test filtering trials by recruitment status"""
        url = reverse('trial-search') + '?status=Recruiting'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['title'], self.trial1.title)
        
    def test_combined_search_and_status(self):
        """Test combined search and status filtering"""
        url = reverse('trial-search') + '?search=COVID&status=Completed'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['title'], self.trial3.title)
