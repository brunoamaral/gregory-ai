"""Tests for the make_active / make_inactive bulk actions on SubscriberAdmin.

make_inactive is the "Disable all emails" action: it must set the global account
flag AND opt the subscriber out of every list, so the global switch and per-list
state stay consistent (no drift). make_active only clears the flag — it must NOT
re-subscribe anyone.
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from organizations.models import Organization
from gregory.models import Team
from subscriptions.models import Lists, Subscribers, ListSubscription

CHANGELIST_URL = reverse('admin:subscriptions_subscribers_changelist')


def _post_action(client, action, pks):
	return client.post(CHANGELIST_URL, {
		'action': action,
		'_selected_action': [str(pk) for pk in pks],
	}, follow=False)


class SubscriberAdminActionTests(TestCase):
	def setUp(self):
		self.superuser = User.objects.create_superuser('super', 'super@example.com', 'pass')
		self.org = Organization.objects.create(name='Org')
		self.team = Team.objects.create(organization=self.org, name='Team', slug='team')
		self.list_a = Lists.objects.create(list_name='List A', team=self.team)
		self.list_b = Lists.objects.create(list_name='List B', team=self.team)
		self.client = Client()
		self.client.force_login(self.superuser)

		self.sub = Subscribers.objects.create(
			first_name='J', last_name='D', email='j@example.com', active=True,
		)
		self.ls_a = ListSubscription.objects.create(subscriber=self.sub, list=self.list_a, is_active=True)
		self.ls_b = ListSubscription.objects.create(subscriber=self.sub, list=self.list_b, is_active=True)

	def test_make_inactive_sets_flag_and_unsubscribes_all_lists(self):
		_post_action(self.client, 'make_inactive', [self.sub.pk])

		self.sub.refresh_from_db()
		self.ls_a.refresh_from_db()
		self.ls_b.refresh_from_db()
		self.assertFalse(self.sub.active)
		self.assertFalse(self.ls_a.is_active)
		self.assertFalse(self.ls_b.is_active)
		self.assertIsNotNone(self.ls_a.unsubscribed_at)
		self.assertIsNotNone(self.ls_b.unsubscribed_at)

	def test_make_inactive_preserves_existing_unsubscribed_at(self):
		stamp = timezone.now() - timedelta(days=100)
		self.ls_a.unsubscribed_at = stamp
		self.ls_a.save(update_fields=['unsubscribed_at'])

		_post_action(self.client, 'make_inactive', [self.sub.pk])

		self.ls_a.refresh_from_db()
		self.assertFalse(self.ls_a.is_active)
		self.assertEqual(self.ls_a.unsubscribed_at, stamp)

	def test_make_inactive_only_affects_selected_subscribers(self):
		other = Subscribers.objects.create(
			first_name='K', last_name='E', email='k@example.com', active=True,
		)
		other_ls = ListSubscription.objects.create(subscriber=other, list=self.list_a, is_active=True)

		_post_action(self.client, 'make_inactive', [self.sub.pk])

		other.refresh_from_db()
		other_ls.refresh_from_db()
		self.assertTrue(other.active)
		self.assertTrue(other_ls.is_active)

	def test_make_active_does_not_resubscribe(self):
		_post_action(self.client, 'make_inactive', [self.sub.pk])
		_post_action(self.client, 'make_active', [self.sub.pk])

		self.sub.refresh_from_db()
		self.ls_a.refresh_from_db()
		self.assertTrue(self.sub.active)
		# Re-activation clears the global flag but must NOT re-subscribe to lists.
		self.assertFalse(self.ls_a.is_active)
