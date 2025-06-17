import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.test import TestCase
from organizations.models import Organization
from gregory.models import Team
from subscriptions.models import Lists, Subscribers

class SubscribersModelTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name='Test Org')
		self.team = Team.objects.create(organization=self.org, name='Alpha', slug='alpha')

	def test_email_saved_lowercase(self):
		subscriber = Subscribers.objects.create(first_name='John', last_name='Doe', email='TEST@EXAMPLE.COM')
		self.assertEqual(subscriber.email, 'test@example.com')

	def test_str_representation(self):
		subscriber = Subscribers.objects.create(first_name='John', last_name='Doe', email='john@example.com')
		self.assertEqual(str(subscriber), 'John Doe (john@example.com)')

class ListsModelTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name='Test Org')
		self.team = Team.objects.create(organization=self.org, name='Alpha', slug='alpha')

	def test_str_representation(self):
		lst = Lists.objects.create(list_name='Daily', team=self.team)
		self.assertEqual(str(lst), 'Daily (Team: Alpha)')
