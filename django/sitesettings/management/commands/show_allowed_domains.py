"""
Management command: show_allowed_domains

Prints the effective ALLOWED_HOSTS and CSRF_TRUSTED_ORIGINS for the running
configuration, broken down by source (static env-var entries vs. values
dynamically loaded from the database at startup).

Usage:
    docker exec gregory python manage.py show_allowed_domains
    docker exec gregory python manage.py show_allowed_domains --verbose
"""

import warnings

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand

from sitesettings.models import CustomSetting


class Command(BaseCommand):
	help = 'Show effective ALLOWED_HOSTS and CSRF_TRUSTED_ORIGINS, including domains loaded from Site objects and CustomSetting records.'

	def add_arguments(self, parser):
		parser.add_argument(
			'--verbose', '-v2',
			action='store_true',
			default=False,
			help='Show each domain with its source (Site, api_domain, allowed_domains, or static).',
		)

	def handle(self, *args, **options):
		verbose = options['verbose']

		# Collect DB-sourced domains annotated by their source
		# host_sources: hostname -> source label (for ALLOWED_HOSTS annotation)
		# csrf_sources: hostname -> source label (for CSRF_TRUSTED_ORIGINS annotation)
		host_sources = {}
		csrf_sources = {}

		with warnings.catch_warnings():
			warnings.simplefilter('ignore', RuntimeWarning)

			for domain in Site.objects.values_list('domain', flat=True):
				domain = domain.strip() if domain else ''
				if domain:
					host_sources[domain] = 'Site.domain'
					csrf_sources[domain] = 'Site.domain'

			for api_domain in CustomSetting.objects.exclude(api_domain='').values_list('api_domain', flat=True):
				api_domain = api_domain.strip() if api_domain else ''
				if api_domain:
					host_sources.setdefault(api_domain, 'CustomSetting.api_domain')
					csrf_sources.setdefault(api_domain, 'CustomSetting.api_domain')

			for raw in CustomSetting.objects.exclude(allowed_domains='').values_list('allowed_domains', flat=True):
				for part in raw.split(','):
					domain = part.strip()
					if domain and '.' in domain:
						host_sources.setdefault(domain, 'CustomSetting.allowed_domains')

			for raw in CustomSetting.objects.exclude(csrf_trusted_origins='').values_list('csrf_trusted_origins', flat=True):
				for part in raw.split(','):
					origin = part.strip()
					if not origin:
						continue
					hostname = origin.removeprefix('https://').removeprefix('http://')
					if '.' in hostname:
						csrf_sources.setdefault(hostname, 'CustomSetting.csrf_trusted_origins')

		self.stdout.write(self.style.SUCCESS('\n=== ALLOWED_HOSTS ==='))
		for host in settings.ALLOWED_HOSTS:
			if verbose:
				source = host_sources.get(host, 'static / env')
				self.stdout.write(f'  {host}  ({source})')
			else:
				self.stdout.write(f'  {host}')

		self.stdout.write(self.style.SUCCESS('\n=== CSRF_TRUSTED_ORIGINS ==='))
		for origin in settings.CSRF_TRUSTED_ORIGINS:
			if verbose:
				hostname = origin.removeprefix('https://').removeprefix('http://')
				source = csrf_sources.get(hostname, 'static / env')
				self.stdout.write(f'  {origin}  ({source})')
			else:
				self.stdout.write(f'  {origin}')

		db_count = len(set(host_sources) | set(csrf_sources))
		self.stdout.write('')
		self.stdout.write(
			f'Total: {len(settings.ALLOWED_HOSTS)} ALLOWED_HOSTS, '
			f'{len(settings.CSRF_TRUSTED_ORIGINS)} CSRF_TRUSTED_ORIGINS '
			f'({db_count} from database)'
		)
