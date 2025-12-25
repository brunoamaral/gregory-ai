from django.core.management.base import BaseCommand
from gregory.models import Authors


class Command(BaseCommand):
	help = 'Delete authors that do not have any article relationship'

	def add_arguments(self, parser):
		parser.add_argument(
			'--dry-run',
			action='store_true',
			help='Show which authors would be deleted without actually deleting them',
		)
		parser.add_argument(
			'--force',
			action='store_true',
			help='Skip confirmation prompt and delete immediately',
		)

	def handle(self, *args, **options):
		dry_run = options['dry_run']
		force = options['force']

		# Find authors with no articles
		orphan_authors = Authors.objects.filter(articles__isnull=True)
		count = orphan_authors.count()

		if count == 0:
			self.stdout.write(self.style.SUCCESS('No orphan authors found. All authors have at least one article.'))
			return

		self.stdout.write(f'Found {count} author(s) without any article relationship:')
		self.stdout.write('')

		# List the authors
		for author in orphan_authors[:50]:  # Show first 50
			orcid_info = f' | ORCID: {author.ORCID}' if author.ORCID else ''
			self.stdout.write(f'  - ID: {author.author_id} | Name: {author.full_name or f"{author.given_name} {author.family_name}"}{orcid_info}')

		if count > 50:
			self.stdout.write(f'  ... and {count - 50} more')

		self.stdout.write('')

		if dry_run:
			self.stdout.write(self.style.WARNING(f'DRY RUN: Would delete {count} author(s). No changes made.'))
			return

		# Confirmation prompt unless --force is used
		if not force:
			self.stdout.write(self.style.WARNING(f'This will permanently delete {count} author(s).'))
			confirm = input('Are you sure you want to proceed? (yes/no): ')
			if confirm.lower() != 'yes':
				self.stdout.write(self.style.WARNING('Operation cancelled.'))
				return

		# Delete the orphan authors
		deleted_count, deleted_details = orphan_authors.delete()
		
		self.stdout.write(self.style.SUCCESS(f'Successfully deleted {deleted_count} object(s).'))
		
		# Show deletion details
		for model, count in deleted_details.items():
			self.stdout.write(f'  - {model}: {count}')
