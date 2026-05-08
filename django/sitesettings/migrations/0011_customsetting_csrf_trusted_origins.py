from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('sitesettings', '0010_copy_allowed_domains_from_lists'),
	]

	operations = [
		migrations.AddField(
			model_name='customsetting',
			name='csrf_trusted_origins',
			field=models.TextField(
				blank=True,
				default='',
				help_text='Comma-separated list of origins (e.g. https://partner.example.com) that Django should trust for cross-site POST requests (CSRF). Only add domains that are authorised to make credentialed requests to this Django backend.',
				verbose_name='CSRF Trusted Origins',
			),
		),
	]
