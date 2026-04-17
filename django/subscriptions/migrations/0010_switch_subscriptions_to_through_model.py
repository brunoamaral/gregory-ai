"""
Switches the Subscribers.subscriptions M2M to use the explicit ListSubscription
through-model.  This must run after migration 0009 which copies existing M2M
rows from the old auto-generated table into ListSubscription.

Django does not support AlterField on M2M fields when adding a through=.
We use RemoveField (which drops the old auto table) + AddField (which adds the
M2M field pointing to the existing ListSubscription table).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('subscriptions', '0009_backfill_unsubscribe_tokens_and_list_subscriptions'),
	]

	operations = [
		# Drop the old auto-generated M2M table (subscriptions_subscribers_subscriptions).
		# By migration 0009 all rows have already been copied to ListSubscription.
		migrations.RemoveField(
			model_name='subscribers',
			name='subscriptions',
		),
		# Re-add the M2M field pointing to the through-model.
		# No new DB table is created — ListSubscription already exists.
		migrations.AddField(
			model_name='subscribers',
			name='subscriptions',
			field=models.ManyToManyField(blank=True, through='subscriptions.ListSubscription', to='subscriptions.lists'),
		),
	]
