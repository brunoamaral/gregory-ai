# Generated manually 2026-04-18
#
# Copies Postmark and ORCID credentials stored on TeamCredentials (legacy)
# to their new canonical locations:
#   - Postmark token  → CustomSetting for each site used by the team's lists
#   - ORCID creds     → OrganizationCredentials for the team's organisation
#
# Existing values are never overwritten (copy-if-empty semantics).
# Uses live model imports so that EncryptedTextField en/decryption works.

from django.db import migrations


def copy_credentials_forward(apps, schema_editor):
	# Import live models so EncryptedTextField decrypt/encrypt works correctly.
	from gregory.models import TeamCredentials, OrganizationCredentials
	from sitesettings.models import CustomSetting

	for tc in TeamCredentials.objects.select_related(
		'team__organization',
	).prefetch_related('team__lists__site__customsetting_set'):

		team = tc.team

		# --- Postmark: copy to CustomSetting for each site used by the team's lists ---
		if tc.postmark_api_token:
			site_ids_seen = set()
			for lst in team.lists.select_related('site').all():
				if not lst.site_id or lst.site_id in site_ids_seen:
					continue
				site_ids_seen.add(lst.site_id)
				try:
					cs = CustomSetting.objects.get(site_id=lst.site_id)
				except CustomSetting.DoesNotExist:
					continue
				if not cs.postmark_api_token:
					cs.postmark_api_token = tc.postmark_api_token
					cs.postmark_api_url = cs.postmark_api_url or tc.postmark_api_url
					cs.save(update_fields=['postmark_api_token', 'postmark_api_url'])

		# --- ORCID: copy to OrganizationCredentials if the org has none ---
		if tc.orcid_client_id and tc.orcid_client_secret:
			try:
				org_creds = team.organization.credentials
				if not org_creds.orcid_client_id:
					org_creds.orcid_client_id = tc.orcid_client_id
					org_creds.orcid_client_secret = tc.orcid_client_secret
					org_creds.save(update_fields=['orcid_client_id', 'orcid_client_secret'])
			except OrganizationCredentials.DoesNotExist:
				OrganizationCredentials.objects.create(
					organization=team.organization,
					orcid_client_id=tc.orcid_client_id,
					orcid_client_secret=tc.orcid_client_secret,
				)


class Migration(migrations.Migration):

	dependencies = [
		('sitesettings', '0005_customsetting_postmark_api_token_and_more'),
		('gregory', '0039_add_team_is_active'),
		('subscriptions', '0017_alter_lists_site'),
	]

	operations = [
		migrations.RunPython(copy_credentials_forward, migrations.RunPython.noop),
	]
