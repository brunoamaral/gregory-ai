from django.core.management.base import BaseCommand
from gregory.models import Articles


class Command(BaseCommand):
	help = 'Delete articles that do not have any source relationship'

	def add_arguments(self, parser):
		parser.add_argument(
			'--dry-run',
			action='store_true',
			help='Show which articles would be deleted without actually deleting them',
		)
		parser.add_argument(
			'--force',
			action='store_true',
			help='Skip confirmation prompt and delete immediately',
		)

	def handle(self, *args, **options):
		dry_run = options['dry_run']
		force = options['force']

		# Find articles with no sources
		orphan_articles = Articles.objects.filter(sources__isnull=True)
		count = orphan_articles.count()

		if count == 0:
			self.stdout.write(self.style.SUCCESS('No orphan articles found. All articles have at least one source.'))
			return

		self.stdout.write(f'Found {count} article(s) without any source relationship:')
		self.stdout.write('')

		# List the articles
		for article in orphan_articles[:50]:  # Show first 50
			self.stdout.write(f'  - ID: {article.article_id} | Title: {article.title[:80]}...' if len(article.title) > 80 else f'  - ID: {article.article_id} | Title: {article.title}')

		if count > 50:
			self.stdout.write(f'  ... and {count - 50} more')

		self.stdout.write('')

		if dry_run:
			self.stdout.write(self.style.WARNING(f'DRY RUN: Would delete {count} article(s). No changes made.'))
			return

		# Confirmation prompt unless --force is used
		if not force:
			self.stdout.write(self.style.WARNING(f'This will permanently delete {count} article(s).'))
			confirm = input('Are you sure you want to proceed? (yes/no): ')
			if confirm.lower() != 'yes':
				self.stdout.write(self.style.WARNING('Operation cancelled.'))
				return

		# Delete the orphan articles
		deleted_count, deleted_details = orphan_articles.delete()
		
		self.stdout.write(self.style.SUCCESS(f'Successfully deleted {deleted_count} object(s).'))
		
		# Show deletion details
		for model, count in deleted_details.items():
			self.stdout.write(f'  - {model}: {count}')
