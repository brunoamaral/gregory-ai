import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from io import StringIO
from django.core.management import call_command
from django.test import TestCase
from organizations.models import Organization
from gregory.models import Team, Articles, Trials
from subscriptions.models import Lists, Subscribers, SentArticleNotification, SentTrialNotification

class MarkAllAsSentCommandTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name='Org')
		self.team = Team.objects.create(organization=self.org, name='Alpha', slug='alpha')
		self.article = Articles.objects.create(title='Art', link='http://a')
		self.trial = Trials.objects.create(title='Tri', link='http://t')
		self.list = Lists.objects.create(list_name='Daily', team=self.team)
		self.subscriber = Subscribers.objects.create(first_name='Bob', email='bob@example.com')
		self.subscriber.subscriptions.add(self.list)

	def test_command_creates_notifications(self):
		out = StringIO()
		call_command('mark_all_as_sent', stdout=out)
		self.assertEqual(SentArticleNotification.objects.count(), 1)
		self.assertEqual(SentTrialNotification.objects.count(), 1)
