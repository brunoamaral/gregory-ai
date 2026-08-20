from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from subscriptions.models import EmailMessage


class Command(BaseCommand):
	help = (
		"Prunes old EmailMessage rows (one per message handed to Postmark). "
		"By default, keeps the last 730 days. Never deletes a row an "
		"AuthorOutreach references — see the AuthorOutreach exclusion note "
		"below. INVARIANT: pruning telemetry must never weaken a "
		"suppression. AuthorContactOptOut (AUTHOR-OUTREACH-PLAN.md PR 2) and "
		"SuppressionEvent are never pruned, by this or any command."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--days",
			type=int,
			default=730,
			help="Number of days of EmailMessage rows to keep (default: 730)",
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

		queryset = EmailMessage.objects.filter(sent_at__lt=cutoff_date)

		# AuthorOutreach (AUTHOR-OUTREACH-PLAN.md PR 3) points an
		# `email_message` FK at the EmailMessage that carried a one-time
		# outreach contact. That row is the durable evidence the contact
		# happened under legitimate interest, so it must survive this
		# command regardless of age — see AUTHOR-OUTREACH-SPEC.md's
		# retention table ("EmailMessage ... never when referenced by an
		# AuthorOutreach").
		#
		# AuthorOutreach doesn't exist until PR 3, so this import is
		# guarded and is a deliberate no-op until then: on this branch
		# (PR 1) every EmailMessage row older than --days is prunable,
		# because nothing can reference one yet. PR 3 makes the exclusion
		# real automatically, with no change needed here.
		try:
			from subscriptions.models import AuthorOutreach
		except ImportError:
			AuthorOutreach = None

		if AuthorOutreach is not None:
			referenced_ids = AuthorOutreach.objects.exclude(
				email_message__isnull=True
			).values_list("email_message_id", flat=True)
			queryset = queryset.exclude(pk__in=referenced_ids)

		count = queryset.count()

		if dry_run:
			self.stdout.write(
				self.style.WARNING("DRY RUN - No records will be deleted")
			)
			self.stdout.write(
				f"Would delete {count:,} EmailMessage rows older than {days} "
				f"days (before {cutoff_date.date()})"
			)
			return

		self.stdout.write(
			f"Pruning EmailMessage rows older than {days} days "
			f"(before {cutoff_date.date()})"
		)
		deleted = self._batch_delete(queryset)
		self.stdout.write(
			self.style.SUCCESS(f"Successfully deleted {deleted:,} EmailMessage rows")
		)

	def _batch_delete(self, queryset, batch_size=10000):
		"""Delete records in batches to avoid memory issues."""
		total_count = queryset.count()

		if total_count == 0:
			self.stdout.write("  No EmailMessage rows to delete")
			return 0

		self.stdout.write(f"  Deleting {total_count:,} EmailMessage rows...")

		deleted_count = 0
		while True:
			batch_ids = list(queryset.values_list("id", flat=True)[:batch_size])
			if not batch_ids:
				break

			deleted, _ = queryset.model.objects.filter(id__in=batch_ids).delete()
			deleted_count += deleted

			if deleted_count % 50000 == 0:
				self.stdout.write(
					f"    Deleted {deleted_count:,} / {total_count:,} EmailMessage rows"
				)

		self.stdout.write(f"  Deleted {deleted_count:,} EmailMessage rows")
		return deleted_count
