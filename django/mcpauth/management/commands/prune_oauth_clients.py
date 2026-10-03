"""
prune_oauth_clients
===================
Delete self-registered OAuth clients that have gone unused.

Clients register themselves (RFC 7591 and Client ID Metadata Documents, D20),
so nothing else ever cleans them up. A client counts as used when it was
registered recently, or when a token or authorization code was issued to it
recently. Clients created by hand in the admin are never touched.

Usage
-----
    python manage.py prune_oauth_clients
    python manage.py prune_oauth_clients --days 120 --dry-run

Meant for cron, daily or weekly.
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from oauth2_provider.models import get_access_token_model, get_application_model, get_grant_model


class Command(BaseCommand):
	help = "Delete self-registered OAuth clients with no token or authorization issued recently."

	def add_arguments(self, parser):
		parser.add_argument(
			"--days",
			type=int,
			default=settings.OAUTH_CLIENT_UNUSED_DAYS,
			help="Unused for this many days (default: %(default)s).",
		)
		parser.add_argument("--dry-run", action="store_true", help="Report what would be deleted.")

	def handle(self, *args, **options):
		days = options["days"]
		if days <= 0:
			raise CommandError("--days must be a positive integer.")
		cutoff = timezone.now() - timedelta(days=days)

		Application = get_application_model()
		self_registered = Application.objects.exclude(registration_source=Application.RegistrationSource.MANUAL)
		stale = self_registered.filter(created__lt=cutoff).exclude(
			pk__in=get_access_token_model().objects.filter(created__gte=cutoff).values("application_id")
		).exclude(
			pk__in=get_grant_model().objects.filter(created__gte=cutoff).values("application_id")
		)

		count = stale.count()
		if options["dry_run"]:
			self.stdout.write(f"Would delete {count} unused client(s) (dry run)")
			return
		stale.delete()
		self.stdout.write(self.style.SUCCESS(f"Deleted {count} client(s) unused for {days} days"))
