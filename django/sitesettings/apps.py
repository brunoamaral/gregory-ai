from django.apps import AppConfig


class SitesettingsConfig(AppConfig):
	default_auto_field = 'django.db.models.BigAutoField'
	name = 'sitesettings'

	def ready(self):
		self._populate_allowed_hosts()

	def _populate_allowed_hosts(self):
		"""
		Append to ALLOWED_HOSTS and CSRF_TRUSTED_ORIGINS at startup using the
		domains stored in the sites framework and CustomSetting records.

		ALLOWED_HOSTS sources (actual Django server hostnames):
		  1. Site.domain
		  2. CustomSetting.api_domain
		  3. CustomSetting.allowed_domains — partner/embedding domains whose requests
		     the Django server must accept (Host header check); dot-validated to skip
		     malformed entries.

		CSRF_TRUSTED_ORIGINS sources (origins trusted for credentialed cross-site POSTs):
		  1. Site.domain
		  2. CustomSetting.api_domain
		  3. CustomSetting.csrf_trusted_origins  ← explicit opt-in field only

		Note: CustomSetting.allowed_domains is NOT added to CSRF_TRUSTED_ORIGINS —
		subscription-form partner domains should not automatically receive CSRF trust.

		Wrapped in a broad try/except so ``manage.py migrate`` on a fresh DB (where
		the sites/sitesettings tables don't yet exist) doesn't crash.
		"""
		import warnings

		try:
			from django.conf import settings
			from django.contrib.sites.models import Site
			from sitesettings.models import CustomSetting

			# Hosts that are actual Django server hostnames
			# (valid values for the Host header → go into ALLOWED_HOSTS)
			allowed_hosts = set()

			# Origins explicitly trusted for CSRF
			# (credentialed cross-site POSTs → go into CSRF_TRUSTED_ORIGINS)
			csrf_origins = set()

			with warnings.catch_warnings():
				warnings.simplefilter('ignore', RuntimeWarning)

				for domain in Site.objects.values_list('domain', flat=True):
					domain = domain.strip() if domain else ''
					if domain:
						allowed_hosts.add(domain)
						csrf_origins.add(domain)

				for api_domain in CustomSetting.objects.exclude(api_domain='').values_list('api_domain', flat=True):
					api_domain = api_domain.strip() if api_domain else ''
					if api_domain:
						allowed_hosts.add(api_domain)
						csrf_origins.add(api_domain)

				for raw in CustomSetting.objects.exclude(allowed_domains='').values_list('allowed_domains', flat=True):
					for part in raw.split(','):
						domain = part.strip()
						if domain and '.' in domain:
							allowed_hosts.add(domain)

				# csrf_trusted_origins is an explicit opt-in for CSRF only;
				# values may be full origins (https://...) or bare hostnames.
				for raw in CustomSetting.objects.exclude(csrf_trusted_origins='').values_list('csrf_trusted_origins', flat=True):
					for part in raw.split(','):
						origin = part.strip()
						if not origin:
							continue
						# Normalise: strip scheme so we store a bare hostname
						# and re-add https:// when writing CSRF_TRUSTED_ORIGINS.
						hostname = origin.removeprefix('https://').removeprefix('http://')
						if '.' in hostname:
							csrf_origins.add(hostname)

			# Append new entries only — preserve existing static values
			current_hosts = set(settings.ALLOWED_HOSTS)
			current_csrf = set(settings.CSRF_TRUSTED_ORIGINS)

			for host in allowed_hosts:
				if host not in current_hosts:
					settings.ALLOWED_HOSTS.append(host)

			for host in csrf_origins:
				https_origin = f'https://{host}'
				if https_origin not in current_csrf:
					settings.CSRF_TRUSTED_ORIGINS.append(https_origin)

		except Exception:
			# Gracefully handle fresh DB (missing tables), test environments, etc.
			pass
