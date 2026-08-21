from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from subscriptions.models import EmailEvent


class Command(BaseCommand):
	help = (
		"Prunes old EmailEvent rows (the Postmark webhook log: Delivery, "
		"Bounce, SpamComplaint, Open, Click — NOT SubscriptionChange, "
		"which is recorded only in SuppressionEvent and never pruned). By "
		"default, keeps the last 180 days — operational telemetry for "
		"deliverability debugging, not a permanent record. INVARIANT: "
		"pruning telemetry must never weaken a suppression. This command "
		"only ever touches EmailEvent — AuthorContactOptOut (added in "
		"docs/author-outreach.md) and SuppressionEvent are never "
		"pruned, by this or any command, because every fact that has to "
		"survive already lives outside EmailEvent."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--days",
			type=int,
			default=180,
			help="Number of days of EmailEvent rows to keep (default: 180)",
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Show what would be deleted without actually deleting",
		)

	def handle(self, *args, **options):
		days = options["days"]
		dry_run = options["dry_run"]
		cutoff_date = timezone.now() - timedelta(days=days)

		queryset = EmailEvent.objects.filter(occurred_at__lt=cutoff_date)
		count = queryset.count()

		if dry_run:
			self.stdout.write(
				self.style.WARNING("DRY RUN - No records will be deleted")
			)
			self.stdout.write(
				f"Would delete {count:,} EmailEvent rows older than {days} "
				f"days (before {cutoff_date.date()})"
			)
			return

		self.stdout.write(
			f"Pruning EmailEvent rows older than {days} days "
			f"(before {cutoff_date.date()})"
		)
		deleted = self._batch_delete(queryset)
		self.stdout.write(
			self.style.SUCCESS(f"Successfully deleted {deleted:,} EmailEvent rows")
		)

	def _batch_delete(self, queryset, batch_size=10000):
		"""Delete records in batches to avoid memory issues."""
		total_count = queryset.count()

		if total_count == 0:
			self.stdout.write("  No EmailEvent rows to delete")
			return 0

		self.stdout.write(f"  Deleting {total_count:,} EmailEvent rows...")

		deleted_count = 0
		while True:
			batch_ids = list(queryset.values_list("id", flat=True)[:batch_size])
			if not batch_ids:
				break

			deleted, _ = queryset.model.objects.filter(id__in=batch_ids).delete()
			deleted_count += deleted

			if deleted_count % 50000 == 0:
				self.stdout.write(
					f"    Deleted {deleted_count:,} / {total_count:,} EmailEvent rows"
				)

		self.stdout.write(f"  Deleted {deleted_count:,} EmailEvent rows")
		return deleted_count
