from django.core.management.base import BaseCommand
from django.core.management import call_command

class Command(BaseCommand):
	help = 'Runs a list of specified management commands.'

	def handle(self, *args, **options):
		commands_to_run = [
			'feedreader_articles',  # 1. Feedreader. Get articles
			'feedreader_trials',		# 2. Feedreader. Get trials
			'find_doi',         		# 3. Find missing DOI
			'update_articles_info', # 4. Find missing data
			'get_authors',      		# 5. Find missing authors
			'update_orcid',     		# 6. Find missing ORCID for authors
			'rebuild_categories',   # 7. Assign categories
			'get_takeaways',    		# 8. Get takeaways
			'3_predict',        		# 9. Predict
		]

		for cmd in commands_to_run:
			try:
				self.stdout.write(self.style.SUCCESS(f'Running command: {cmd}'))
				call_command(cmd)
				self.stdout.write(self.style.SUCCESS(f'Finished command: {cmd}'))
			except Exception as e:
				self.stderr.write(self.style.ERROR(f'Error running command {cmd}: {str(e)}'))
				self.stdout.write(self.style.WARNING(f'Skipping command: {cmd}'))