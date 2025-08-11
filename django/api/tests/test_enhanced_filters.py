from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from gregory.models import Trials, Authors, Sources, TeamCategory, Team, Subject, Organization
from django.utils import timezone
import json

class TrialFilterTests(TestCase):
    """Test cases for enhanced trial filtering"""
    
    def setUp(self):
        # Create test organization, team and subject
        self.organization = Organization.objects.create(name="Test Organization")
        self.team = Team.objects.create(name="Test Team", organization=self.organization)
        self.subject = Subject.objects.create(
            subject_name="Test Subject",
            subject_slug="test-subject",
            team=self.team
        )
        
        # Create test trials with various fields
        self.trial1 = Trials.objects.create(
            title="COVID-19 Vaccine Trial",
            summary="Testing efficacy of mRNA vaccines for coronavirus.",
            link="https://example.com/trial1",
            published_date=timezone.now(),
            recruitment_status="Recruiting",
            internal_number="INT-2024-001",
            phase="Phase III",
            study_type="Interventional",
            primary_sponsor="Big Pharma Inc",
            source_register="ClinicalTrials.gov",
            countries="United States, Canada",
            condition="COVID-19",
            intervention="mRNA Vaccine",
            therapeutic_areas="Infectious Diseases",
            inclusion_agemin="18",
            inclusion_agemax="65",
            inclusion_gender="All"
        )
        self.trial1.teams.add(self.team)
        self.trial1.subjects.add(self.subject)
        
        self.trial2 = Trials.objects.create(
            title="Multiple Sclerosis Treatment Trial",
            summary="New MS medication phase 2 study.",
            link="https://example.com/trial2",
            published_date=timezone.now(),
            recruitment_status="Active, not recruiting",
            internal_number="INT-2024-002",
            phase="Phase II",
            study_type="Observational",
            primary_sponsor="University Medical Center",
            source_register="EudraCT",
            countries="Germany, France",
            condition="Multiple Sclerosis",
            intervention="Novel Drug",
            therapeutic_areas="Neurology",
            inclusion_agemin="21",
            inclusion_agemax="60",
            inclusion_gender="Female"
        )
        self.trial2.teams.add(self.team)
        self.trial2.subjects.add(self.subject)
        
        self.client = APIClient()
    
    def test_trial_id_filter(self):
        """Test filtering trials by trial_id"""
        response = self.client.get(f'/trials/?trial_id={self.trial1.trial_id}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['trial_id'], self.trial1.trial_id)
    
    def test_internal_number_filter(self):
        """Test filtering trials by internal_number"""
        response = self.client.get('/trials/?internal_number=INT-2024-001')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['trial_id'], self.trial1.trial_id)
    
    def test_phase_filter(self):
        """Test filtering trials by phase"""
        response = self.client.get('/trials/?phase=Phase III')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['trial_id'], self.trial1.trial_id)
    
    def test_study_type_filter(self):
        """Test filtering trials by study_type"""
        response = self.client.get('/trials/?study_type=Interventional')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['trial_id'], self.trial1.trial_id)
    
    def test_primary_sponsor_filter(self):
        """Test filtering trials by primary_sponsor"""
        response = self.client.get('/trials/?primary_sponsor=Big Pharma')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['trial_id'], self.trial1.trial_id)
    
    def test_source_register_filter(self):
        """Test filtering trials by source_register"""
        response = self.client.get('/trials/?source_register=ClinicalTrials.gov')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['trial_id'], self.trial1.trial_id)
    
    def test_countries_filter(self):
        """Test filtering trials by countries"""
        response = self.client.get('/trials/?countries=United States')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['trial_id'], self.trial1.trial_id)
    
    def test_condition_filter(self):
        """Test filtering trials by condition"""
        response = self.client.get('/trials/?condition=COVID-19')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['trial_id'], self.trial1.trial_id)
    
    def test_intervention_filter(self):
        """Test filtering trials by intervention"""
        response = self.client.get('/trials/?intervention=mRNA')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['trial_id'], self.trial1.trial_id)
    
    def test_therapeutic_areas_filter(self):
        """Test filtering trials by therapeutic_areas"""
        response = self.client.get('/trials/?therapeutic_areas=Infectious')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['trial_id'], self.trial1.trial_id)
    
    def test_inclusion_age_filters(self):
        """Test filtering trials by inclusion age criteria"""
        response = self.client.get('/trials/?inclusion_agemin=18')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['trial_id'], self.trial1.trial_id)
        
        response = self.client.get('/trials/?inclusion_agemax=65')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['trial_id'], self.trial1.trial_id)
    
    def test_inclusion_gender_filter(self):
        """Test filtering trials by inclusion_gender"""
        response = self.client.get('/trials/?inclusion_gender=Female')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['trial_id'], self.trial2.trial_id)
    
    def test_recruitment_status_vs_status(self):
        """Test both recruitment_status and status filters work"""
        # Test new recruitment_status filter
        response = self.client.get('/trials/?recruitment_status=Recruiting')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['trial_id'], self.trial1.trial_id)
        
        # Test legacy status filter for backward compatibility
        response = self.client.get('/trials/?status=Recruiting')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['trial_id'], self.trial1.trial_id)
    
    def test_combined_filters(self):
        """Test combining multiple filters"""
        response = self.client.get('/trials/?phase=Phase III&condition=COVID-19&countries=United States')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['trial_id'], self.trial1.trial_id)
        
        # Test combination that should return no results
        response = self.client.get('/trials/?phase=Phase III&condition=Multiple Sclerosis')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 0)


class AuthorFilterTests(TestCase):
    """Test cases for enhanced author filtering"""
    
    def setUp(self):
        # Clear any existing authors to avoid interference
        Authors.objects.all().delete()
        
        # Create test authors
        self.author1 = Authors.objects.create(
            family_name="Smith",
            given_name="John",
            full_name="John Smith",
            ORCID="0000-0000-0000-0001",
            country="US"
        )
        self.author2 = Authors.objects.create(
            family_name="Doe",
            given_name="Jane",
            full_name="Jane Doe",
            ORCID="0000-0000-0000-0002",
            country="GB"
        )
        
        self.client = APIClient()
    
    def test_orcid_filter(self):
        """Test filtering authors by ORCID"""
        response = self.client.get('/authors/?orcid=0000-0000-0000-0001')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['author_id'], self.author1.author_id)
    
    def test_orcid_partial_filter(self):
        """Test filtering authors by partial ORCID"""
        response = self.client.get('/authors/?orcid=0001')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['author_id'], self.author1.author_id)
    
    def test_country_filter(self):
        """Test filtering authors by country"""
        response = self.client.get('/authors/?country=US')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['author_id'], self.author1.author_id)
    
    def test_combined_author_filters(self):
        """Test combining author filters"""
        response = self.client.get('/authors/?country=US&orcid=0000-0000-0000-0001')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['author_id'], self.author1.author_id)


class SourceFilterTests(TestCase):
    """Test cases for enhanced source filtering"""
    
    def setUp(self):
        # Create test data
        self.organization = Organization.objects.create(name="Test Organization")
        self.team = Team.objects.create(name="Test Team", organization=self.organization)
        self.subject = Subject.objects.create(
            subject_name="Test Subject",
            subject_slug="test-subject",
            team=self.team
        )
        
        # Note: We need to check the actual Sources model structure
        # This is a placeholder test structure
        self.client = APIClient()
    
    def test_source_id_filter(self):
        """Test filtering sources by source_id"""
        # This test would need actual Sources objects created
        # Based on the model structure found in the codebase
        pass
    
    def test_source_for_filter(self):
        """Test filtering sources by source_for field"""
        # Test filtering by source_for='articles' or 'trials'
        pass


class CategoryFilterTests(TestCase):
    """Test cases for enhanced category filtering"""
    
    def setUp(self):
        # Create test data
        self.organization = Organization.objects.create(name="Test Organization")
        self.team = Team.objects.create(name="Test Team", organization=self.organization)
        self.subject = Subject.objects.create(
            subject_name="Test Subject",
            subject_slug="test-subject",
            team=self.team
        )
        
        self.category = TeamCategory.objects.create(
            team=self.team,
            category_name="Test Category",
            category_slug="test-category",
            category_terms=["test", "category", "example"]
        )
        self.category.subjects.add(self.subject)
        
        self.client = APIClient()
    
    def test_category_terms_filter(self):
        """Test filtering categories by category_terms"""
        response = self.client.get('/categories/?category_terms=test')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should find the category that contains "test" in its terms
        self.assertGreaterEqual(len(response.data['results']), 1)
