from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from gregory.models import Trials, Team, Subject, Organization
from django.utils import timezone


class TrialIdentifierFilterTests(TestCase):
	"""Test cases for filtering trials by identifier"""
	
	def setUp(self):
		# Create test organization, team and subject
		self.organization = Organization.objects.create(name="Test Organization")
		self.team = Team.objects.create(name="Test Team", organization=self.organization)
		self.subject = Subject.objects.create(
			subject_name="Test Subject",
			subject_slug="test-subject",
			team=self.team
		)
		
		# Create trials with different identifier types
		self.trial_nct = Trials.objects.create(
			title="ClinicalTrials.gov Trial",
			summary="A trial from ClinicalTrials.gov",
			link="https://clinicaltrials.gov/study/NCT12345678",
			published_date=timezone.now(),
			recruitment_status="Recruiting",
			identifiers={"nct": "NCT12345678", "org_study_id": "STUDY-001"}
		)
		self.trial_nct.teams.add(self.team)
		self.trial_nct.subjects.add(self.subject)
		
		self.trial_eudract = Trials.objects.create(
			title="EudraCT Trial",
			summary="A trial from EU registry",
			link="https://example.com/trial2",
			published_date=timezone.now(),
			recruitment_status="Active, not recruiting",
			identifiers={"eudract": "2024-123456-12-34"}
		)
		self.trial_eudract.teams.add(self.team)
		self.trial_eudract.subjects.add(self.subject)
		
		self.trial_euct = Trials.objects.create(
			title="EUCT Trial",
			summary="A trial from EU Clinical Trials",
			link="https://example.com/trial3",
			published_date=timezone.now(),
			recruitment_status="Recruiting",
			identifiers={"euct": "2024-987654-21-43"}
		)
		self.trial_euct.teams.add(self.team)
		self.trial_euct.subjects.add(self.subject)
		
		self.trial_ctis = Trials.objects.create(
			title="CTIS Trial",
			summary="A trial from CTIS",
			link="https://example.com/trial4",
			published_date=timezone.now(),
			recruitment_status="Completed",
			identifiers={"ctis": "CTIS-2024-12345"}
		)
		self.trial_ctis.teams.add(self.team)
		self.trial_ctis.subjects.add(self.subject)
		
		# Trial with no identifiers
		self.trial_no_id = Trials.objects.create(
			title="Trial Without Identifiers",
			summary="A trial with no identifier",
			link="https://example.com/trial5",
			published_date=timezone.now(),
			recruitment_status="Recruiting",
			identifiers={}
		)
		self.trial_no_id.teams.add(self.team)
		self.trial_no_id.subjects.add(self.subject)
		
		self.client = APIClient()
	
	def test_filter_by_nct_identifier(self):
		"""Test filtering by NCT identifier"""
		url = reverse('trials-list')
		response = self.client.get(url, {'identifier': 'NCT12345678'})
		
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data['count'], 1)
		self.assertEqual(response.data['results'][0]['title'], self.trial_nct.title)
	
	def test_filter_by_eudract_identifier(self):
		"""Test filtering by EudraCT identifier"""
		url = reverse('trials-list')
		response = self.client.get(url, {'identifier': '2024-123456-12-34'})
		
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data['count'], 1)
		self.assertEqual(response.data['results'][0]['title'], self.trial_eudract.title)
	
	def test_filter_by_euct_identifier(self):
		"""Test filtering by EUCT identifier"""
		url = reverse('trials-list')
		response = self.client.get(url, {'identifier': '2024-987654-21-43'})
		
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data['count'], 1)
		self.assertEqual(response.data['results'][0]['title'], self.trial_euct.title)
	
	def test_filter_by_ctis_identifier(self):
		"""Test filtering by CTIS identifier"""
		url = reverse('trials-list')
		response = self.client.get(url, {'identifier': 'CTIS-2024-12345'})
		
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data['count'], 1)
		self.assertEqual(response.data['results'][0]['title'], self.trial_ctis.title)
	
	def test_filter_by_org_study_id(self):
		"""Test filtering by organization study ID"""
		url = reverse('trials-list')
		response = self.client.get(url, {'identifier': 'STUDY-001'})
		
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data['count'], 1)
		self.assertEqual(response.data['results'][0]['title'], self.trial_nct.title)
	
	def test_filter_nonexistent_identifier(self):
		"""Test filtering by identifier that doesn't exist"""
		url = reverse('trials-list')
		response = self.client.get(url, {'identifier': 'NONEXISTENT-ID'})
		
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data['count'], 0)
		self.assertEqual(len(response.data['results']), 0)
	
	def test_identifier_filter_with_other_filters(self):
		"""Test combining identifier filter with other filters"""
		url = reverse('trials-list')
		response = self.client.get(url, {
			'identifier': 'NCT12345678',
			'team_id': self.team.id,
			'recruitment_status': 'Recruiting'
		})
		
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data['count'], 1)
		self.assertEqual(response.data['results'][0]['title'], self.trial_nct.title)
	
	def test_identifier_filter_case_sensitive(self):
		"""Test that identifier filter is case-sensitive (exact match)"""
		url = reverse('trials-list')
		
		# Should find with exact case
		response = self.client.get(url, {'identifier': 'NCT12345678'})
		self.assertEqual(response.data['count'], 1)
		
		# Should NOT find with wrong case (PostgreSQL JSON lookups are case-sensitive by default)
		response = self.client.get(url, {'identifier': 'nct12345678'})
		self.assertEqual(response.data['count'], 0)
