# Generated manually on 2026-04-24

from django.db import migrations


class Migration(migrations.Migration):

	dependencies = [
		('subscriptions', '0019_announcement_show_tagline_preheader_text'),
		('sitesettings', '0010_copy_allowed_domains_from_lists'),
	]

	operations = [
		migrations.RemoveField(
			model_name='lists',
			name='allowed_domains',
		),
	]
