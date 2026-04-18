from django.conf import settings
from django.contrib.sites.models import Site

from gregory.models import OrganizationCredentials, OrganizationSite
from sitesettings.models import CustomSetting
import os


def get_orcid_credentials(organization=None):
	"""
	Resolve ORCID API credentials using the fallback chain:
	Organization → environment variables.

	All-or-nothing: if the organization has both client_id and client_secret set,
	use them. Otherwise fall back to environment variables.

	Pass organization=None to skip org lookups and go straight to env vars.

	Returns a tuple (client_id, client_secret).
	"""
	if organization is not None:
		try:
			org_creds = organization.credentials
			if org_creds.orcid_client_id and org_creds.orcid_client_secret:
				return (org_creds.orcid_client_id, org_creds.orcid_client_secret)
		except OrganizationCredentials.DoesNotExist:
			pass

	# Fall back to environment variables
	return (
		os.environ.get('ORCID_CLIENT_ID'),
		os.environ.get('ORCID_CLIENT_SECRET'),
	)


def get_postmark_credentials(custom_settings=None, organization=None):
	"""
	Resolve Postmark credentials using the fallback chain:
	CustomSetting (site-level) → OrganizationCredentials → Django settings.

	All-or-nothing per level: both token and URL must be set to use that level.

	Returns a tuple (api_token, api_url).
	"""
	# Try site-level credentials
	if custom_settings is not None:
		if getattr(custom_settings, 'postmark_api_token', None) and getattr(custom_settings, 'postmark_api_url', None):
			return (custom_settings.postmark_api_token, custom_settings.postmark_api_url)

	# Try organization-level credentials
	if organization is not None:
		try:
			org_creds = organization.credentials
			if org_creds.postmark_api_token and org_creds.postmark_api_url:
				return (org_creds.postmark_api_token, org_creds.postmark_api_url)
		except OrganizationCredentials.DoesNotExist:
			pass

	# Fall back to Django settings
	return (
		getattr(settings, 'EMAIL_POSTMARK_API_KEY', None),
		getattr(settings, 'EMAIL_POSTMARK_API_URL', None),
	)


def get_site_and_settings(team, list_obj=None):
	"""
	Resolve the Site and CustomSetting for an email list (or team) using the
	fallback chain:
	  1. list_obj.site (explicitly configured on the List; auto-populated on save)
	  2. Organization's default OrganizationSite (is_default=True)
	  3. Organization's first OrganizationSite (any)
	  4. Site.objects.get_current() (global SITE_ID fallback)

	Returns a tuple (site, custom_settings).
	Raises CustomSetting.DoesNotExist if no CustomSetting exists for the resolved site.
	"""
	site = None

	if list_obj is not None and list_obj.site_id:
		site = list_obj.site
	else:
		org_site = (
			OrganizationSite.objects
			.filter(organization=team.organization)
			.order_by('-is_default', 'id')
			.select_related('site')
			.first()
		)
		if org_site:
			site = org_site.site

	if site is None:
		site = Site.objects.get_current()

	custom_settings = CustomSetting.objects.get(site=site)
	return (site, custom_settings)
