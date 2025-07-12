from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from gregory.models import Authors, Articles, Team, Subject, Organization
from django.utils import timezone


class AuthorSearchViewTests(TestCase):
	"""Test cases for the author search endpoint"""

	def setUp(self):
		self.organization = Organization.objects.create(name="Test Organization")
		self.team = Team.objects.create(name="Test Team", organization=self.organization)
		self.subject = Subject.objects.create(
			subject_name="Test Subject",
			subject_slug="test-subject",
			team=self.team
		)

		self.author1 = Authors.objects.create(given_name="Jane", family_name="Doe")
		self.author2 = Authors.objects.create(given_name="John", family_name="Smith")

		article = Articles.objects.create(
			title="Test Article",
			summary="Sample",
			link="http://example.com",
			published_date=timezone.now()
		)
		article.authors.add(self.author1, self.author2)
		article.teams.add(self.team)
		article.subjects.add(self.subject)

		self.client = APIClient()

	def test_missing_required_parameters(self):
		url = reverse('author-search')

		response = self.client.post(url, {}, format='json')
		self.assertEqual(response.status_code, 400)

		response = self.client.post(url, {'team_id': self.team.id}, format='json')
		self.assertEqual(response.status_code, 400)

		response = self.client.post(url, {'subject_id': self.subject.id}, format='json')
		self.assertEqual(response.status_code, 400)

	def test_search_by_full_name(self):
		url = reverse('author-search')
		data = {
			'team_id': self.team.id,
			'subject_id': self.subject.id,
			'full_name': 'jane'
		}
		response = self.client.post(url, data, format='json')

		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.data['results']), 1)
		self.assertEqual(response.data['results'][0]['author_id'], self.author1.author_id)
