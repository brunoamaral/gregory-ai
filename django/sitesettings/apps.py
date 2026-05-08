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

		Sources (all deduplicated):
		  1. Site.domain            — primary hostname per site
		  2. CustomSetting.api_domain — API subdomain per site
		  3. CustomSetting.allowed_domains — comma-separated external embedding domains
		     (only entries containing a dot are used, to skip malformed values)

		Wrapped in a broad try/except so ``manage.py migrate`` on a fresh DB (where
		the sites/sitesettings tables don't yet exist) doesn't crash.
		"""
		import warnings

		try:
			from django.conf import settings
			from django.contrib.sites.models import Site
			from sitesettings.models import CustomSetting

			extra_hosts = set()

			# Suppress the "Accessing the database during app initialization" warning
			# because we intentionally query the DB here and already guard against
			# missing tables with the outer try/except.
			with warnings.catch_warnings():
				warnings.simplefilter('ignore', RuntimeWarning)

				for domain in Site.objects.values_list('domain', flat=True):
					if domain:
						extra_hosts.add(domain.strip())

				for api_domain in CustomSetting.objects.exclude(api_domain='').values_list('api_domain', flat=True):
					if api_domain:
						extra_hosts.add(api_domain.strip())

				for raw in CustomSetting.objects.exclude(allowed_domains='').values_list('allowed_domains', flat=True):
					for part in raw.split(','):
						domain = part.strip()
						# Require at least one dot to skip single-label fragments
						# that result from malformed comma-separated entries.
						if domain and '.' in domain:
							extra_hosts.add(domain)

			# Append new entries only — preserve existing static values
			current_hosts = set(settings.ALLOWED_HOSTS)
			current_csrf = set(settings.CSRF_TRUSTED_ORIGINS)

			for host in extra_hosts:
				if host not in current_hosts:
					settings.ALLOWED_HOSTS.append(host)
				https_origin = f'https://{host}'
				if https_origin not in current_csrf:
					settings.CSRF_TRUSTED_ORIGINS.append(https_origin)

		except Exception:
			# Gracefully handle fresh DB (missing tables), test environments, etc.
			pass
