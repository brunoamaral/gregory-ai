import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.test import TestCase, RequestFactory
from django.contrib.sites.models import Site
from django.conf import settings
from organizations.models import Organization
from gregory.models import Team
from subscriptions.models import Lists, Subscribers
from subscriptions.views import subscribe_view

class SubscribeViewTest(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.org = Organization.objects.create(name='Test Org')
		self.team = Team.objects.create(organization=self.org, name='Alpha', slug='alpha')
		Site.objects.update_or_create(id=settings.SITE_ID, defaults={'domain': 'example.com', 'name': 'example'})
		self.lst = Lists.objects.create(list_name='Daily', team=self.team)

	def test_subscribe_new_user(self):
		data = {
			'first_name': 'Alice',
			'last_name': 'Smith',
			'email': 'ALICE@EXAMPLE.COM',
			'profile': 'patient',
			'list': [str(self.lst.pk)]
		}
		request = self.factory.post('/subscribe/', data)
		response = subscribe_view(request)
		self.assertEqual(response.status_code, 302)
		self.assertIn('/thank-you/', response['Location'])
		subscriber = Subscribers.objects.get(email='alice@example.com')
		self.assertIn(self.lst, subscriber.subscriptions.all())

	def test_invalid_form(self):
		data = {
			'first_name': '',
			'last_name': 'Smith',
			'email': 'bademail',
			'profile': 'patient',
			'list': [str(self.lst.pk)]
		}
		request = self.factory.post('/subscribe/', data)
		response = subscribe_view(request)
		self.assertEqual(response.status_code, 302)
		self.assertIn('/error/', response['Location'])

	def test_nonexistent_list_id_redirects_to_error(self):
		"""Posting a list ID that doesn't exist must redirect to /error/ and not create a subscriber."""
		data = {
			'first_name': 'Bob',
			'last_name': 'Jones',
			'email': 'bob@example.com',
			'profile': 'researcher',
			'list': ['99999'],  # does not exist
		}
		request = self.factory.post('/subscribe/', data)
		with self.assertLogs('subscriptions.views', level='ERROR') as log:
			response = subscribe_view(request)
		self.assertEqual(response.status_code, 302)
		self.assertIn('/error/', response['Location'])
		# No subscriber should have been created
		self.assertFalse(Subscribers.objects.filter(email='bob@example.com').exists())
		# Error was logged
		self.assertTrue(any('do not exist in the database' in msg for msg in log.output))

	def test_mixed_valid_invalid_list_ids_redirects_to_error(self):
		"""If any submitted list ID is invalid the whole request should fail."""
		data = {
			'first_name': 'Carol',
			'last_name': 'White',
			'email': 'carol@example.com',
			'profile': 'doctor',
			'list': [str(self.lst.pk), '99999'],
		}
		request = self.factory.post('/subscribe/', data)
		with self.assertLogs('subscriptions.views', level='ERROR') as log:
			response = subscribe_view(request)
		self.assertEqual(response.status_code, 302)
		self.assertIn('/error/', response['Location'])
		self.assertFalse(Subscribers.objects.filter(email='carol@example.com').exists())

	def test_no_list_submitted_redirects_to_error(self):
		"""A form submitted without any list field must redirect to /error/."""
		data = {
			'first_name': 'Dave',
			'last_name': 'Black',
			'email': 'dave@example.com',
			'profile': 'patient',
			# no 'list' key
		}
		request = self.factory.post('/subscribe/', data)
		with self.assertLogs('subscriptions.views', level='ERROR') as log:
			response = subscribe_view(request)
		self.assertEqual(response.status_code, 302)
		self.assertIn('/error/', response['Location'])
		self.assertFalse(Subscribers.objects.filter(email='dave@example.com').exists())
