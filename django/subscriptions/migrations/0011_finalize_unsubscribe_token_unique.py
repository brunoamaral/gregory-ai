"""
Finalizes the unsubscribe_token field: makes it unique and non-nullable now that
migration 0009 has backfilled UUIDs for all existing rows.
"""

import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('subscriptions', '0010_switch_subscriptions_to_through_model'),
	]

	operations = [
		migrations.AlterField(
			model_name='subscribers',
			name='unsubscribe_token',
			field=models.UUIDField(
				default=uuid.uuid4,
				unique=True,
				editable=False,
				help_text='Unique token used in unsubscribe links. Never expose this outside of email context.',
			),
		),
	]
