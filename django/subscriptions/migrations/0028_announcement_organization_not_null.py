"""
Alters the organization FK on Announcement from nullable to NOT NULL.

This migration must run after 0027_backfill_announcement_organization,
which guarantees every Announcement row has an organization_id set.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('subscriptions', '0027_backfill_announcement_organization'),
		('organizations', '0006_alter_organization_slug'),
	]

	operations = [
		migrations.AlterField(
			model_name='announcement',
			name='organization',
			field=models.ForeignKey(
				help_text='The organization that owns this announcement. Determines who can see and edit it.',
				on_delete=django.db.models.deletion.PROTECT,
				related_name='announcements',
				to='organizations.organization',
			),
		),
	]
