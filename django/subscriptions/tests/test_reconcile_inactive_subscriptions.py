"""Tests for the reconcile_inactive_subscriptions management command."""

import os
import django
from datetime import timedelta
from io import StringIO

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")
django.setup()

from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from gregory.models import Team
from organizations.models import Organization
from subscriptions.models import Lists, Subscribers, ListSubscription


class ReconcileInactiveSubscriptionsTests(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Recon Org", slug="recon-org")
		self.team = Team.objects.create(
			name="Recon Team", organization=self.org, slug="recon-team"
		)
		self.site, _ = Site.objects.get_or_create(
			id=1,
			defaults={"domain": "testserver.example.com", "name": "Test Site"},
		)
		self.list_a = Lists.objects.create(list_name="List A", team=self.team)
		self.list_b = Lists.objects.create(list_name="List B", team=self.team)

		# Drifted: inactive account still holding active subscriptions.
		self.inactive_sub = Subscribers.objects.create(
			first_name="In",
			last_name="Active",
			email="inactive@example.com",
			active=False,
		)
		self.drift_a = ListSubscription.objects.create(
			subscriber=self.inactive_sub,
			list=self.list_a,
			is_active=True,
		)
		self.drift_b = ListSubscription.objects.create(
			subscriber=self.inactive_sub,
			list=self.list_b,
			is_active=True,
		)

		# Healthy: active account with an active subscription — must be untouched.
		self.active_sub = Subscribers.objects.create(
			first_name="Act",
			last_name="Ive",
			email="active@example.com",
			active=True,
		)
		self.healthy = ListSubscription.objects.create(
			subscriber=self.active_sub,
			list=self.list_a,
			is_active=True,
		)

	def _run(self, *args):
		out = StringIO()
		call_command("reconcile_inactive_subscriptions", *args, stdout=out)
		return out.getvalue()

	def test_dry_run_reports_but_does_not_change(self):
		output = self._run()  # no --apply
		self.assertIn("Found 2 active subscription(s) across 1", output)
		self.assertIn("Dry run", output)
		# Nothing changed.
		self.drift_a.refresh_from_db()
		self.drift_b.refresh_from_db()
		self.assertTrue(self.drift_a.is_active)
		self.assertTrue(self.drift_b.is_active)
		self.assertIsNone(self.drift_a.unsubscribed_at)

	def test_apply_opts_out_drifted_subscriptions(self):
		output = self._run("--apply")
		self.assertIn("Reconciled 2 subscription(s)", output)

		self.drift_a.refresh_from_db()
		self.drift_b.refresh_from_db()
		self.assertFalse(self.drift_a.is_active)
		self.assertFalse(self.drift_b.is_active)
		self.assertIsNotNone(self.drift_a.unsubscribed_at)
		self.assertIsNotNone(self.drift_b.unsubscribed_at)

	def test_apply_leaves_healthy_subscription_untouched(self):
		self._run("--apply")
		self.healthy.refresh_from_db()
		self.assertTrue(self.healthy.is_active)
		self.assertIsNone(self.healthy.unsubscribed_at)

	def test_existing_unsubscribed_at_is_preserved(self):
		stamp = timezone.now() - timedelta(days=400)
		# An active row that already carries a (stale) unsubscribed_at.
		self.drift_a.unsubscribed_at = stamp
		self.drift_a.save(update_fields=["unsubscribed_at"])

		self._run("--apply")
		self.drift_a.refresh_from_db()
		self.assertFalse(self.drift_a.is_active)
		self.assertEqual(self.drift_a.unsubscribed_at, stamp)

	def test_idempotent_second_run_finds_nothing(self):
		self._run("--apply")
		output = self._run("--apply")
		self.assertIn("Nothing to reconcile", output)
