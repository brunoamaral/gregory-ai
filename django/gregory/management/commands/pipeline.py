from django.core.management.base import BaseCommand
from django.core.management import call_command

class Command(BaseCommand):
	help = 'Runs a list of specified management commands.'

	def handle(self, *args, **options):
		commands_to_run = ['feedreader', 'update_articles_info', 'get_takeaways', 'get_authors', 'update_orcid', 'rebuild_categories', '3_predict']

		for cmd in commands_to_run:
			self.stdout.write(self.style.SUCCESS(f'Running command: {cmd}'))
			call_command(cmd)
			self.stdout.write(self.style.SUCCESS(f'Finished command: {cmd}'))
