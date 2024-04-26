from django.core.management.base import BaseCommand
from django.core.management import call_command

class Command(BaseCommand):
	help = 'Runs a list of specified management commands.'

	def handle(self, *args, **options):
		commands_to_run = [
			# 1. Feedreader. Get articles and try to fetch some data
			'feedreader', 
			# 2. Find missing DOI
			'find_doi',
			# 3. Find missing data
			'update_articles_info', 
			# 4. Find missing authors
			'get_authors', 
			# 5. Find missing ORCID for authors
			'update_orcid',
			# 4. Assign categories
			'rebuild_categories', 
			# 5. Get takeaways
			'get_takeaways',
			# 6. Predict
			'3_predict', ]

		for cmd in commands_to_run:
			self.stdout.write(self.style.SUCCESS(f'Running command: {cmd}'))
			call_command(cmd)
			self.stdout.write(self.style.SUCCESS(f'Finished command: {cmd}'))
