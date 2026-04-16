from django.conf import settings

from gregory.models import TeamCredentials, OrganizationCredentials


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
