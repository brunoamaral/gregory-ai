# Generated manually on 2026-03-21

from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('subscriptions', '0006_alter_historicalsubscribers_profile_and_more'),
	]

	operations = [
		migrations.AddField(
			model_name='lists',
			name='allowed_domains',
			field=models.TextField(
				blank=True,
				default='',
				help_text='Comma-separated list of domains allowed to submit subscribers to this list (e.g. example.com, other-site.org). The origin domain is used for post-subscription redirects.',
				verbose_name='Allowed Domains',
			),
		),
	]
