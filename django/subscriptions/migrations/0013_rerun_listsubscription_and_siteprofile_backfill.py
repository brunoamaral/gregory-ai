"""
Re-runs the ListSubscription and SubscriberSiteProfile backfill from migration
0009, which silently did nothing on databases where the old auto-generated M2M
table was named 'subscribers_subscriptions' instead of
'subscriptions_subscribers_subscriptions'.

Also fixes a silent failure in 0009's create_site_profiles: that function used
historical models, but gregory_team.site was not yet in the model state at
migration 0009. As a result team.site always resolved to None and no profiles
were created.

This migration declares a dependency on gregory/0035_organizationsite and uses
raw SQL so it is not subject to historical model limitations. When a team has
no site_id set, it falls back to the organisation's default site via
OrganizationSite (is_default=True).

Both operations are idempotent (ON CONFLICT DO NOTHING), so running on a
database that was already correctly migrated is safe.
"""

from django.db import migrations


def remigrate_m2m_to_through(apps, schema_editor):
	db = schema_editor.connection

	# Check for both possible names of the old auto-generated M2M table.
	old_table = None
	with db.cursor() as cursor:
		cursor.execute(
			"SELECT table_name FROM information_schema.tables "
			"WHERE table_schema = 'public' "
			"AND table_name IN ('subscriptions_subscribers_subscriptions', 'subscribers_subscriptions')"
		)
		row = cursor.fetchone()
		if row:
			old_table = row[0]

	if not old_table:
		return

	with db.cursor() as cursor:
		cursor.execute(f"""
			INSERT INTO subscriptions_listsubscription
				(subscriber_id, list_id, subscribed_at, consent_method, is_active)
			SELECT subscribers_id, lists_id, NOW(), 'import', true
			FROM {old_table}
			ON CONFLICT (subscriber_id, list_id) DO NOTHING
		""")


def recreate_site_profiles(apps, schema_editor):
	db = schema_editor.connection

	# Use raw SQL so we get the live schema (team.site_id) rather than the
	# historical model state at migration 0009 which predates gregory/0035_organizationsite.
	# When a team has no site_id, fall back to the organisation's default site
	# (OrganizationSite where is_default=true).
	with db.cursor() as cursor:
		cursor.execute("""
			INSERT INTO subscriptions_subscribersiteprofile
				(subscriber_id, site_id, profile, created_at, updated_at)
			SELECT DISTINCT ON (ls.subscriber_id, COALESCE(t.site_id, os.site_id))
				ls.subscriber_id,
				COALESCE(t.site_id, os.site_id),
				COALESCE(NULLIF(sub.profile, ''), 'patient'),
				NOW(),
				NOW()
			FROM subscriptions_listsubscription ls
			JOIN subscriptions_lists l ON l.list_id = ls.list_id
			JOIN gregory_team t ON t.id = l.team_id
			JOIN subscribers sub ON sub.subscriber_id = ls.subscriber_id
			LEFT JOIN gregory_organizationsite os
				ON os.organization_id = t.organization_id AND os.is_default = true
			WHERE COALESCE(t.site_id, os.site_id) IS NOT NULL
			ON CONFLICT (subscriber_id, site_id) DO NOTHING
		""")


class Migration(migrations.Migration):

	dependencies = [
		('subscriptions', '0012_fix_lists_team_fk'),
		('gregory', '0035_organizationsite'),
	]

	operations = [
		migrations.RunPython(remigrate_m2m_to_through, migrations.RunPython.noop),
		migrations.RunPython(recreate_site_profiles, migrations.RunPython.noop),
	]
