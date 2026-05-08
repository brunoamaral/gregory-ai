from django.apps import AppConfig
from urllib.parse import urlparse


def _to_hostname(value):
	"""
	Normalise a raw domain/origin string to a bare lowercase hostname.
	Handles scheme prefixes (https://, http://), port suffixes, and IPv6 brackets.
	Returns None for empty, whitespace-only, or unparseable values.
	"""
	if not value:
		return None
	v = value.strip()
	if not v:
		return None
	# Prefix with // so urlparse treats the value as a netloc, not a path
	if '://' not in v:
		v = f'//{v}'
	return urlparse(v).hostname or None


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
		import logging
		import warnings

		from django.db.utils import OperationalError, ProgrammingError

		logger = logging.getLogger(__name__)

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
					h = _to_hostname(domain)
					if h:
						allowed_hosts.add(h)
						csrf_origins.add(h)

				for api_domain in CustomSetting.objects.exclude(api_domain='').values_list('api_domain', flat=True):
					h = _to_hostname(api_domain)
					if h:
						allowed_hosts.add(h)
						csrf_origins.add(h)

				# allowed_domains: subscription-form origin allowlist; add to ALLOWED_HOSTS
				# only (not CSRF). Dot-check rejects single-label/malformed entries.
				for raw in CustomSetting.objects.exclude(allowed_domains='').values_list('allowed_domains', flat=True):
					for part in raw.split(','):
						h = _to_hostname(part)
						if h and '.' in h:
							allowed_hosts.add(h)

				# csrf_trusted_origins: explicit opt-in for CSRF only.
				for raw in CustomSetting.objects.exclude(csrf_trusted_origins='').values_list('csrf_trusted_origins', flat=True):
					for part in raw.split(','):
						h = _to_hostname(part)
						if h and '.' in h:
							csrf_origins.add(h)

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

		except (OperationalError, ProgrammingError, LookupError):
			# DB not ready yet: fresh install, manage.py migrate, or app not registered.
			pass
		except Exception:
			logger.warning(
				'sitesettings: unexpected error while populating ALLOWED_HOSTS/CSRF_TRUSTED_ORIGINS',
				exc_info=True,
			)
