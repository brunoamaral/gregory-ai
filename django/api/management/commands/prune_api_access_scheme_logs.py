from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from api.models import APIAccessSchemeLog


RETENTION_WINDOWS = {
	"30d": 30,
	"90d": 90,
	"1y": 365,
}


class Command(BaseCommand):
	help = (
		"Prune API access scheme logs by retention timeframe. "
		"Supported windows: 30d, 90d, 1y."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--window",
			choices=RETENTION_WINDOWS.keys(),
			default="30d",
			help="Retention window to keep (default: 30d)",
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Show how many logs would be deleted without deleting",
		)

	def handle(self, *args, **options):
		window = options["window"]
		dry_run = options["dry_run"]

		cutoff_date = timezone.now() - timedelta(days=RETENTION_WINDOWS[window])
		old_logs = APIAccessSchemeLog.objects.filter(access_date__lt=cutoff_date)
		old_logs_count = old_logs.count()

		self.stdout.write(
			f"Pruning API access scheme logs for window {window} "
			f"(deleting before {cutoff_date.strftime('%Y-%m-%d %H:%M:%S')})"
		)

		if dry_run:
			self.stdout.write(self.style.WARNING("DRY RUN: no logs will be deleted"))
			self.stdout.write(f"Would delete {old_logs_count} log(s)")
			return

		if old_logs_count == 0:
			self.stdout.write("No logs to delete")
			return

		deleted_count, _ = old_logs.delete()
		self.stdout.write(self.style.SUCCESS(f"Deleted {deleted_count} log(s)"))
