import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.test import TestCase
from organizations.models import Organization
from gregory.models import Team, TeamCategory, TeamCredentials

class TeamCategoryTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name='Test Org')
		self.team = Team.objects.create(organization=self.org, name='Alpha', slug='alpha')

	def test_slug_auto_generation(self):
		cat = TeamCategory.objects.create(team=self.team, category_name='Neuro Science')
		self.assertEqual(cat.category_slug, 'neuro-science')

class EncryptedFieldTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name='Test Org')
		self.team = Team.objects.create(organization=self.org, name='Alpha', slug='alpha')

	def test_encryption_roundtrip(self):
		cred = TeamCredentials.objects.create(team=self.team, postmark_api_token='secret')
		stored = TeamCredentials.objects.filter(pk=cred.pk).values_list('postmark_api_token', flat=True).get()
		self.assertNotEqual(stored, 'secret')
		obj = TeamCredentials.objects.get(pk=cred.pk)
		self.assertEqual(obj.postmark_api_token, 'secret')
