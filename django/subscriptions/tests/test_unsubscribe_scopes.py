import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.test import TestCase, RequestFactory
from django.contrib.sites.models import Site
from organizations.models import Organization
from gregory.models import Team
from subscriptions.models import Lists, Subscribers, ListSubscription
from subscriptions.views import unsubscribe_list, unsubscribe_site, unsubscribe_all


class UnsubscribeScopesTest(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.org = Organization.objects.create(name="Test Org")

		# Two sites, so we can prove the site-scope filter is site-specific.
		self.site_a, _ = Site.objects.get_or_create(
			id=101, defaults={"domain": "site-a.example.com", "name": "Site A"}
		)
		self.site_b, _ = Site.objects.get_or_create(
			id=102, defaults={"domain": "site-b.example.com", "name": "Site B"}
		)

		# Reproduce the exact production shape: team.site is None or a
		# *different* value than list.site. The site-scope filter must not
		# depend on team.site at all.
		self.team_no_site = Team.objects.create(
			organization=self.org, name="Team No Site", slug="team-no-site", site=None
		)
		self.team_other_site = Team.objects.create(
			organization=self.org,
			name="Team Other Site",
			slug="team-other-site",
			site=self.site_b,
		)

		self.list1 = Lists.objects.create(
			list_name="List 1", team=self.team_no_site, site=self.site_a
		)
		self.list2 = Lists.objects.create(
			list_name="List 2", team=self.team_other_site, site=self.site_a
		)
		self.list_other_site = Lists.objects.create(
			list_name="List Other Site", team=self.team_no_site, site=self.site_b
		)

		self.subscriber = Subscribers.objects.create(
			first_name="Alice", last_name="Smith", email="alice@example.com"
		)
		self.sub1 = ListSubscription.objects.create(
			subscriber=self.subscriber, list=self.list1, is_active=True
		)
		self.sub2 = ListSubscription.objects.create(
			subscriber=self.subscriber, list=self.list2, is_active=True
		)
		self.sub_other_site = ListSubscription.objects.create(
			subscriber=self.subscriber, list=self.list_other_site, is_active=True
		)

	def _post_site(self, site_id):
		request = self.factory.post(f"/subscriptions/unsubscribe/x/site/{site_id}/")
		return unsubscribe_site(request, self.subscriber.unsubscribe_token, site_id)

	def _post_list(self, list_id):
		request = self.factory.post(f"/subscriptions/unsubscribe/x/list/{list_id}/")
		return unsubscribe_list(request, self.subscriber.unsubscribe_token, list_id)

	def _post_all(self):
		request = self.factory.post("/subscriptions/unsubscribe/x/all/")
		return unsubscribe_all(request, self.subscriber.unsubscribe_token)

	def test_site_scope_deactivates_every_list_on_that_site_across_teams(self):
		response = self._post_site(self.site_a.pk)
		self.assertEqual(response.status_code, 200)

		self.sub1.refresh_from_db()
		self.sub2.refresh_from_db()
		self.assertFalse(self.sub1.is_active)
		self.assertFalse(self.sub2.is_active)

	def test_site_scope_leaves_lists_on_other_sites_untouched(self):
		self._post_site(self.site_a.pk)

		self.sub_other_site.refresh_from_db()
		self.assertTrue(self.sub_other_site.is_active)

	def test_site_scope_regression_ignores_team_site(self):
		"""
		Production shape: list.site is set, team.site is None or a
		different site. The old code filtered on list__team__site_id and
		matched nothing. This must deactivate based on list.site alone.
		"""
		# list1's team has site=None, list2's team has site=site_b (different
		# from list2.site=site_a) -- both must still be deactivated.
		response = self._post_site(self.site_a.pk)
		self.assertEqual(response.status_code, 200)

		self.sub1.refresh_from_db()
		self.sub2.refresh_from_db()
		self.assertFalse(self.sub1.is_active)
		self.assertFalse(self.sub2.is_active)

	def test_site_scope_stamps_unsubscribed_at_and_leaves_subscriber_active(self):
		self._post_site(self.site_a.pk)

		self.sub1.refresh_from_db()
		self.subscriber.refresh_from_db()
		self.assertIsNotNone(self.sub1.unsubscribed_at)
		self.assertTrue(self.subscriber.active)

	def test_list_scope_keeps_current_behaviour(self):
		response = self._post_list(self.list1.pk)
		self.assertEqual(response.status_code, 200)

		self.sub1.refresh_from_db()
		self.sub2.refresh_from_db()
		self.assertFalse(self.sub1.is_active)
		self.assertTrue(self.sub2.is_active)

	def test_all_scope_keeps_current_behaviour(self):
		response = self._post_all()
		self.assertEqual(response.status_code, 200)

		self.subscriber.refresh_from_db()
		self.sub1.refresh_from_db()
		self.sub2.refresh_from_db()
		self.sub_other_site.refresh_from_db()
		self.assertFalse(self.subscriber.active)
		self.assertFalse(self.sub1.is_active)
		self.assertFalse(self.sub2.is_active)
		self.assertFalse(self.sub_other_site.is_active)

	def test_updated_count_zero_renders_nothing_to_unsubscribe_variant(self):
		# Deactivate everything first so the second request matches zero rows.
		self._post_site(self.site_a.pk)

		response = self._post_site(self.site_a.pk)
		self.assertEqual(response.status_code, 200)
		content = response.content.decode()
		self.assertIn("was not subscribed to anything on this site", content)

	def test_updated_count_zero_for_list_scope(self):
		self._post_list(self.list1.pk)

		response = self._post_list(self.list1.pk)
		self.assertEqual(response.status_code, 200)
		content = response.content.decode()
		self.assertIn("was not subscribed to this mailing list", content)

	def test_all_scope_reports_success_even_when_updated_count_is_zero(self):
		# No active subscriptions at all, but scope=all must still report
		# success because the account flag is the meaningful action.
		ListSubscription.objects.filter(subscriber=self.subscriber).update(
			is_active=False
		)
		response = self._post_all()
		self.assertEqual(response.status_code, 200)
		content = response.content.decode()
		self.assertIn("globally unsubscribed", content)
