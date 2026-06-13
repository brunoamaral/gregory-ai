"""Reconcile list subscriptions for globally-inactive subscribers.

A subscriber's ``active`` flag is a global email switch: when ``active=False`` they
receive no email from any list (see Subscribers.active help text). That flag can be
set without touching their per-list ``ListSubscription`` rows — e.g. via the admin
"Disable all emails" bulk action, which uses ``queryset.update(active=False)``. The
result is "drift": an inactive account that still holds ``is_active=True``
subscriptions. Those subscriptions are stale (the person is already suppressed from
all email) and cause analytics to over-count active subscribers.

This command brings subscription state in line with the global opt-out: for every
active subscription belonging to an inactive account, it sets ``is_active=False`` and
stamps ``unsubscribed_at`` (preserving any existing timestamp). It never re-enables
email for anyone, so it is GDPR-safe.

Dry-run by default; pass ``--apply`` to write changes. The command is idempotent.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from subscriptions.models import ListSubscription


class Command(BaseCommand):
	help = (
		"Opt out stale active subscriptions held by globally-inactive subscribers "
		"(active=False). Dry-run by default; pass --apply to write changes."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--apply",
			action="store_true",
			help="Write the changes. Without this flag the command only reports (dry run).",
		)

	def handle(self, *args, **options):
		apply_changes = options["apply"]

		# Active subscriptions belonging to a globally-inactive account.
		stale_qs = ListSubscription.objects.filter(
			is_active=True,
			subscriber__active=False,
		)

		affected_rows = stale_qs.count()
		affected_subscribers = stale_qs.values("subscriber_id").distinct().count()

		if affected_rows == 0:
			self.stdout.write(
				self.style.SUCCESS(
					"Nothing to reconcile — no active subscriptions on inactive accounts."
				)
			)
			return

		self.stdout.write(
			f"Found {affected_rows} active subscription(s) across {affected_subscribers} "
			f"inactive subscriber account(s)."
		)

		if not apply_changes:
			for ls in stale_qs.select_related("subscriber", "list")[:10]:
				self.stdout.write(
					f"  would opt-out: {ls.subscriber.email} → {ls.list.list_name}"
				)
			if affected_rows > 10:
				self.stdout.write(f"  … and {affected_rows - 10} more")
			self.stdout.write(
				self.style.WARNING(
					"Dry run — no changes written. Re-run with --apply to commit."
				)
			)
			return

		now = timezone.now()
		with transaction.atomic():
			# Stamp unsubscribed_at only where missing, then flip is_active.
			stale_qs.filter(unsubscribed_at__isnull=True).update(unsubscribed_at=now)
			updated = stale_qs.update(is_active=False)

		self.stdout.write(
			self.style.SUCCESS(
				f"Reconciled {updated} subscription(s) across {affected_subscribers} account(s): "
				f"set is_active=False and stamped unsubscribed_at where missing."
			)
		)
