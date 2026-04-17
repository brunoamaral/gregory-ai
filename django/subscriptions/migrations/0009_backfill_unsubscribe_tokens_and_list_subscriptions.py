"""
Data migration that:
1. Backfills unsubscribe_token for all existing Subscribers rows (callable uuid4
   default only fires for new rows, not existing ones).
2. Migrates existing Subscribers↔Lists M2M rows from the old auto-generated
   through table (subscriptions_subscribers_subscriptions) into ListSubscription,
   marking them consent_method='import'.
3. Creates SubscriberSiteProfile rows for existing subscribers by inferring the
   site from their list's team (team.site). Skips subscribers/lists where no
   site can be resolved.
"""

import uuid
from django.db import migrations


def backfill_tokens(apps, schema_editor):
	Subscribers = apps.get_model('subscriptions', 'Subscribers')
	for sub in Subscribers.objects.filter(unsubscribe_token__isnull=True):
		sub.unsubscribe_token = uuid.uuid4()
		sub.save(update_fields=['unsubscribe_token'])


def migrate_m2m_to_through(apps, schema_editor):
	"""
	The old auto M2M table was dropped by migration 0008. At that point Django
	keeps the rows in the database but the through-model is now ListSubscription.
	However, when the AlterField ran, existing rows in the implicit table are NOT
	automatically moved; they are orphaned. We need to read directly from the raw
	table and recreate them.
	"""
	ListSubscription = apps.get_model('subscriptions', 'ListSubscription')
	db = schema_editor.connection

	# First check if the old auto M2M table still exists (it won't on a fresh install).
	# We use information_schema to avoid aborting the current PostgreSQL transaction.
	# The table may be named 'subscriptions_subscribers_subscriptions' (app-prefixed)
	# or 'subscribers_subscriptions' (legacy name from an older schema).
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
		cursor.execute(
			f"SELECT subscribers_id, lists_id FROM {old_table}"
		)
		rows = cursor.fetchall()

	for subscriber_id, list_id in rows:
		ListSubscription.objects.get_or_create(
			subscriber_id=subscriber_id,
			list_id=list_id,
			defaults={
				'consent_method': 'import',
				'is_active': True,
			},
		)


def create_site_profiles(apps, schema_editor):
	Subscribers = apps.get_model('subscriptions', 'Subscribers')
	SubscriberSiteProfile = apps.get_model('subscriptions', 'SubscriberSiteProfile')
	ListSubscription = apps.get_model('subscriptions', 'ListSubscription')

	for sub in Subscribers.objects.all():
		# Collect all unique sites this subscriber is associated with via their lists
		site_profile_map = {}  # site_id → profile
		for ls in ListSubscription.objects.filter(subscriber=sub).select_related('list__team__site'):
			team = ls.list.team
			if team is None:
				continue
			site = getattr(team, 'site', None)
			if site is None:
				continue
			# Use the subscriber's global profile as a starting value
			if site.id not in site_profile_map:
				site_profile_map[site.id] = sub.profile

		for site_id, profile in site_profile_map.items():
			SubscriberSiteProfile.objects.get_or_create(
				subscriber=sub,
				site_id=site_id,
				defaults={'profile': profile},
			)


class Migration(migrations.Migration):

	dependencies = [
		('subscriptions', '0008_add_list_subscription_and_site_profile'),
	]

	operations = [
		migrations.RunPython(backfill_tokens, migrations.RunPython.noop),
		migrations.RunPython(migrate_m2m_to_through, migrations.RunPython.noop),
		migrations.RunPython(create_site_profiles, migrations.RunPython.noop),
	]
