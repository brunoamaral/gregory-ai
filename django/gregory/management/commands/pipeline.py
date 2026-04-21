from django.core.management.base import BaseCommand
from django.core.management import call_command

class Command(BaseCommand):
	help = 'Runs a list of specified management commands.'

	def add_arguments(self, parser):
		parser.add_argument(
			'--recent-days',
			type=int,
			default=30,
			help='Number of days to look back when processing trial references (default: 30)'
		)

	def handle(self, *args, **options):
		# standard commands to run in the pipeline
		commands_to_run = [
			'feedreader_articles',  # 1. Feedreader. Get articles
			'feedreader_trials',		# 2. Feedreader. Get trials
			'feedreader_trials_ctgov', # 3. ClinicalTrials.gov trials
			'find_doi',         		# 4. Find missing DOI
			'update_articles_info', # 5. Find missing data
			'get_authors',      		# 6. Find missing authors
			'rebuild_categories',   # 7. Assign categories
			'get_takeaways',    		# 8. Get takeaways
		]

		# First run all the standard commands
		for cmd in commands_to_run:
			try:
				self.stdout.write(self.style.SUCCESS(f'Running command: {cmd}'))
				call_command(cmd)
				self.stdout.write(self.style.SUCCESS(f'Finished command: {cmd}'))
			except Exception as e:
				self.stderr.write(self.style.ERROR(f'Error running command {cmd}: {str(e)}'))
				self.stdout.write(self.style.WARNING(f'Skipping command: {cmd}'))

		# Run update_orcid per organisation — each org uses its own ORCID credentials
		from django.apps import apps
		Organization = apps.get_model('organizations', 'Organization')
		for org in Organization.objects.all().order_by('slug'):
			creds = getattr(org, 'credentials', None)
			if not creds or not creds.orcid_client_id or not creds.orcid_client_secret:
				self.stdout.write(self.style.WARNING(
					f'Skipping update_orcid for organisation "{org.slug}": no ORCID credentials configured.'
				))
				continue
			try:
				self.stdout.write(self.style.SUCCESS(f'Running update_orcid for organisation: {org.slug}'))
				call_command('update_orcid', organization=org.slug)
			except Exception as e:
				self.stderr.write(self.style.ERROR(f'Error running update_orcid for {org.slug}: {e}'))
		
		# Now run predict_articles with the --all-teams flag
		try:
			self.stdout.write(self.style.SUCCESS('Running predict_articles with --all-teams flag'))
			call_command('predict_articles', all_teams=True)
			self.stdout.write(self.style.SUCCESS('Finished running predict_articles with --all-teams flag'))
		except Exception as e:
			self.stderr.write(self.style.ERROR(f'Error running predict_articles: {str(e)}'))

		# Run detect_trial_references with recent articles only
		try:
			days = options.get('recent_days', 30)
			self.stdout.write(self.style.SUCCESS(f'Running detect_trial_references for articles from the last {days} days'))
			call_command('detect_trial_references', recent=True, days=days)
			self.stdout.write(self.style.SUCCESS('Finished running detect_trial_references'))
		except Exception as e:
			self.stderr.write(self.style.ERROR(f'Error running detect_trial_references: {str(e)}'))

		# Prune old sent notification records (keeps last 30 days by default)
		try:
			self.stdout.write(self.style.SUCCESS('Running prune_sent_notifications to clean up old records'))
			call_command('prune_sent_notifications', days=30)
			self.stdout.write(self.style.SUCCESS('Finished running prune_sent_notifications'))
		except Exception as e:
			self.stderr.write(self.style.ERROR(f'Error running prune_sent_notifications: {str(e)}'))