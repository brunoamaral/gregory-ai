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
