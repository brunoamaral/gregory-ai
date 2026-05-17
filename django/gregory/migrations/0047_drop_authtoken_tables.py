"""
Drop the authtoken tables left behind when rest_framework.authtoken is removed
from INSTALLED_APPS.  Django does not auto-generate a migration for app removal,
so we do it manually via RunSQL.

The two tables are:
  authtoken_token   — one token per user
  authtoken_tokenproxy — Django content-type proxy table (may or may not exist)

Both DROP statements use IF EXISTS so the migration is safe to run on databases
that never had the authtoken app installed.
"""
from django.db import migrations


class Migration(migrations.Migration):

	dependencies = [
		('gregory', '0046_historicalarticleorgcontent_api_access_scheme_label_and_more'),
	]

	operations = [
		migrations.RunSQL(
			sql=[
				'DROP TABLE IF EXISTS authtoken_token CASCADE;',
				'DROP TABLE IF EXISTS authtoken_tokenproxy CASCADE;',
			],
			# We can't recover the dropped rows, but make the migration
			# reversible (no-op on the way back) so `manage.py migrate
			# gregory 0046` doesn't fail with IrreversibleError.  Restoring
			# the tables would require re-adding rest_framework.authtoken
			# to INSTALLED_APPS, which is the deliberate way to roll back.
			reverse_sql=migrations.RunSQL.noop,
		),
	]
