from django.core.management.base import BaseCommand
from django.core.management import call_command

class Command(BaseCommand):
	help = 'Runs a list of specified management commands.'

	def handle(self, *args, **options):
		commands_to_run = [
			'feedreader',       		# 1. Feedreader. Get articles and try to fetch some data
			'find_doi',         		# 2. Find missing DOI
			'update_articles_info', # 3. Find missing data
			'get_authors',      		# 4. Find missing authors
			'update_orcid',     		# 5. Find missing ORCID for authors
			'rebuild_categories',   # 6. Assign categories
			'get_takeaways',    		# 7. Get takeaways
			'3_predict',        		# 8. Predict
		]

		for cmd in commands_to_run:
			try:
				self.stdout.write(self.style.SUCCESS(f'Running command: {cmd}'))
				call_command(cmd)
				self.stdout.write(self.style.SUCCESS(f'Finished command: {cmd}'))
			except Exception as e:
				self.stderr.write(self.style.ERROR(f'Error running command {cmd}: {str(e)}'))
				self.stdout.write(self.style.WARNING(f'Skipping command: {cmd}'))