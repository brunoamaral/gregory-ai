from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from subscriptions.models import SentArticleNotification, SentTrialNotification


class Command(BaseCommand):
	help = 'Prunes old sent notification records that are no longer needed for deduplication. By default, keeps the last 30 days.'

	def add_arguments(self, parser):
		parser.add_argument(
			'--days',
			type=int,
			default=30,
			help='Number of days of notification records to keep (default: 30)'
		)
		parser.add_argument(
			'--dry-run',
			action='store_true',
			help='Show what would be deleted without actually deleting'
		)
		parser.add_argument(
			'--articles-only',
			action='store_true',
			help='Only prune article notifications'
		)
		parser.add_argument(
			'--trials-only',
			action='store_true',
			help='Only prune trial notifications'
		)

	def handle(self, *args, **options):
		days = options['days']
		dry_run = options['dry_run']
		articles_only = options['articles_only']
		trials_only = options['trials_only']

		cutoff_date = timezone.now() - timedelta(days=days)

		if dry_run:
			self.stdout.write(self.style.WARNING('DRY RUN - No records will be deleted'))

		self.stdout.write(f'Pruning notification records older than {days} days (before {cutoff_date.date()})')

		total_deleted = 0

		# Prune article notifications
		if not trials_only:
			article_notifs = SentArticleNotification.objects.filter(sent_at__lt=cutoff_date)
			article_count = article_notifs.count()

			if dry_run:
				self.stdout.write(f'  Would delete {article_count:,} article notification records')
			else:
				# Delete in batches to avoid memory issues with large datasets
				deleted_articles = self._batch_delete(article_notifs, 'article notifications')
				total_deleted += deleted_articles

		# Prune trial notifications
		if not articles_only:
			trial_notifs = SentTrialNotification.objects.filter(sent_at__lt=cutoff_date)
			trial_count = trial_notifs.count()

			if dry_run:
				self.stdout.write(f'  Would delete {trial_count:,} trial notification records')
			else:
				deleted_trials = self._batch_delete(trial_notifs, 'trial notifications')
				total_deleted += deleted_trials

		if dry_run:
			self.stdout.write(self.style.SUCCESS('Dry run complete. No records were deleted.'))
		else:
			self.stdout.write(self.style.SUCCESS(f'Successfully deleted {total_deleted:,} old notification records'))

	def _batch_delete(self, queryset, description, batch_size=10000):
		"""Delete records in batches to avoid memory issues."""
		total_count = queryset.count()
		
		if total_count == 0:
			self.stdout.write(f'  No {description} to delete')
			return 0

		self.stdout.write(f'  Deleting {total_count:,} {description}...')
		
		deleted_count = 0
		while True:
			# Get batch of IDs to delete
			batch_ids = list(queryset.values_list('id', flat=True)[:batch_size])
			if not batch_ids:
				break
			
			# Delete the batch
			deleted, _ = queryset.model.objects.filter(id__in=batch_ids).delete()
			deleted_count += deleted
			
			if deleted_count % 50000 == 0:
				self.stdout.write(f'    Deleted {deleted_count:,} / {total_count:,} {description}')

		self.stdout.write(f'  Deleted {deleted_count:,} {description}')
		return deleted_count
