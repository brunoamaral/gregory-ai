# Generated manually on 2026-04-24

from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('sitesettings', '0008_remove_customsetting_email_footer'),
	]

	operations = [
		migrations.AddField(
			model_name='customsetting',
			name='allowed_domains',
			field=models.TextField(
				blank=True,
				default='',
				help_text='Comma-separated list of domains (e.g. example.com, other-site.org) allowed to submit subscribers for any list on this site. The origin domain is used for post-subscription redirects. The site\'s own domain is always accepted.',
				verbose_name='Allowed Domains',
			),
		),
	]
