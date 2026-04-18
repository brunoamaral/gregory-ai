# Generated manually 2026-04-18
#
# Copies Postmark and ORCID credentials stored on TeamCredentials (legacy)
# to their new canonical locations:
#   - Postmark token  → CustomSetting for each site used by the team's lists
#   - ORCID creds     → OrganizationCredentials for the team's organisation
#
# Existing values are never overwritten (copy-if-empty semantics).
# Self-contained: uses apps.get_model() throughout and inlines encrypt/decrypt
# helpers so the migration does not depend on any live model import.

import base64

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import migrations
from django.utils import timezone


# ---------------------------------------------------------------------------
# Inline encryption helpers — intentionally NOT imported from gregory.models
# so this migration remains self-contained even if EncryptedTextField moves.
# ---------------------------------------------------------------------------

def _get_fernet():
	return Fernet(settings.FERNET_SECRET_KEY)


def _decrypt(raw_value):
	"""Decrypt a base64-encoded Fernet token. Returns None if blank."""
	if not raw_value:
		return None
	return _get_fernet().decrypt(base64.b64decode(raw_value)).decode()


def _encrypt(plaintext):
	"""Return a base64-encoded Fernet token. Returns None if blank."""
	if not plaintext:
		return None
	return base64.b64encode(_get_fernet().encrypt(plaintext.encode())).decode()


def copy_credentials_forward(apps, schema_editor):
	OrganizationCredentials = apps.get_model('gregory', 'OrganizationCredentials')
	CustomSetting = apps.get_model('sitesettings', 'CustomSetting')
	Lists = apps.get_model('subscriptions', 'Lists')
	Team = apps.get_model('gregory', 'Team')

	# Table name for TeamCredentials (removed from live models, use literal name).
	tc_table = 'gregory_teamcredentials'
	cs_table = CustomSetting._meta.db_table
	oc_table = OrganizationCredentials._meta.db_table

	# Read TeamCredentials via raw SQL so that EncryptedTextField.from_db_value is
	# never invoked.  Django calls from_db_value even in .values() queries, which
	# would decrypt the data before _decrypt() sees it, causing a double-decrypt
	# failure (InvalidToken / incorrect padding).
	with schema_editor.connection.cursor() as cursor:
		cursor.execute(
			f"SELECT id, team_id, postmark_api_token, postmark_api_url,"
			f" orcid_client_id, orcid_client_secret FROM {tc_table}"
		)
		cols = [c[0] for c in cursor.description]
		tc_rows = [dict(zip(cols, row)) for row in cursor.fetchall()]

	for tc_row in tc_rows:
		postmark_token = _decrypt(tc_row['postmark_api_token'])
		postmark_url = tc_row.get('postmark_api_url') or ''

		# --- Postmark: copy to CustomSetting for each site the team's lists use ---
		if postmark_token:
			site_ids = list(
				Lists.objects
				.filter(team_id=tc_row['team_id'])
				.exclude(site_id=None)
				.values_list('site_id', flat=True)
				.distinct()
			)
			if site_ids:
				placeholders = ', '.join(['%s'] * len(site_ids))
				with schema_editor.connection.cursor() as cursor:
					cursor.execute(
						f"SELECT setting_id, postmark_api_token, postmark_api_url"
						f" FROM {cs_table} WHERE site_id IN ({placeholders})",
						site_ids,
					)
					cs_cols = [c[0] for c in cursor.description]
					cs_rows = [dict(zip(cs_cols, row)) for row in cursor.fetchall()]
				for cs_row in cs_rows:
					if not cs_row['postmark_api_token']:
						schema_editor.execute(
							f"UPDATE {cs_table}"
							" SET postmark_api_token = %s"
							", postmark_api_url = COALESCE(NULLIF(postmark_api_url, ''), %s)"
							" WHERE setting_id = %s",
							[_encrypt(postmark_token), postmark_url, cs_row['setting_id']],
						)

		# --- ORCID: copy to OrganizationCredentials for the team's organisation ---
		orcid_id = _decrypt(tc_row['orcid_client_id'])
		orcid_secret = _decrypt(tc_row['orcid_client_secret'])

		if not (orcid_id and orcid_secret):
			continue

		try:
			org_id = Team.objects.values_list('organization_id', flat=True).get(
				pk=tc_row['team_id']
			)
		except Team.DoesNotExist:
			continue

		# Read OrganizationCredentials via raw SQL for the same reason.
		with schema_editor.connection.cursor() as cursor:
			cursor.execute(
				f"SELECT id, orcid_client_id FROM {oc_table} WHERE organization_id = %s",
				[org_id],
			)
			oc_row = cursor.fetchone()

		if oc_row:
			existing_id, existing_orcid_id = oc_row
			if not _decrypt(existing_orcid_id):
				schema_editor.execute(
					f"UPDATE {oc_table}"
					" SET orcid_client_id = %s, orcid_client_secret = %s"
					" WHERE id = %s",
					[_encrypt(orcid_id), _encrypt(orcid_secret), existing_id],
				)
		else:
			now = timezone.now()
			schema_editor.execute(
				f"INSERT INTO {oc_table}"
				" (organization_id, orcid_client_id, orcid_client_secret, created_at, updated_at)"
				" VALUES (%s, %s, %s, %s, %s)",
				[org_id, _encrypt(orcid_id), _encrypt(orcid_secret), now, now],
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
