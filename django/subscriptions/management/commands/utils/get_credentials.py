from django.conf import settings
from django.contrib.sites.models import Site

from gregory.models import TeamCredentials, OrganizationCredentials, OrganizationSite
from sitesettings.models import CustomSetting


def get_postmark_credentials(team):
	"""
	Resolve Postmark credentials using the fallback chain:
	Team → Organization → Django settings.

	All-or-nothing: if the team has credentials with both token and URL set,
	use them. Otherwise fall back to the organization, then Django settings.

	Returns a tuple (api_token, api_url).
	"""
	# Try team-level credentials
	try:
		creds = team.credentials
		if creds.postmark_api_token and creds.postmark_api_url:
			return (creds.postmark_api_token, creds.postmark_api_url)
	except TeamCredentials.DoesNotExist:
		pass

	# Try organization-level credentials
	try:
		org_creds = team.organization.credentials
		if org_creds.postmark_api_token and org_creds.postmark_api_url:
			return (org_creds.postmark_api_token, org_creds.postmark_api_url)
	except OrganizationCredentials.DoesNotExist:
		pass

	# Fall back to Django settings
	return (
		getattr(settings, 'EMAIL_POSTMARK_API_KEY', None),
		getattr(settings, 'EMAIL_POSTMARK_API_URL', None),
	)


def get_site_and_settings(team):
	"""
	Resolve the Site and CustomSetting for a team using the fallback chain:
	  1. team.site (explicitly configured on the Team)
	  2. Organization's default OrganizationSite (is_default=True)
	  3. Organization's first OrganizationSite (any)
	  4. Site.objects.get_current() (global SITE_ID fallback)

	Returns a tuple (site, custom_settings).
	Raises CustomSetting.DoesNotExist if no CustomSetting exists for the resolved site.
	"""
	site = None

	if team.site_id:
		site = team.site
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
