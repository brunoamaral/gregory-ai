import django.db.models.deletion
from django.db import migrations, models


def backfill_api_settings(apps, schema_editor):
	"""Create OrganizationApiSettings rows for all pre-existing organisations.

	Existing orgs are assumed to have been publicly accessible before this flag
	existed, so we backfill with make_api_public=True.  Orgs created after this
	migration will be handled by the post_save signal and will default to False.
	"""
	Organization = apps.get_model('organizations', 'Organization')
	OrganizationApiSettings = apps.get_model('gregory', 'OrganizationApiSettings')
	for org in Organization.objects.all():
		OrganizationApiSettings.objects.get_or_create(
			organization=org,
			defaults={'make_api_public': True},
		)


def reverse_backfill(apps, schema_editor):
	"""Remove all OrganizationApiSettings rows (used when reversing the migration)."""
	OrganizationApiSettings = apps.get_model('gregory', 'OrganizationApiSettings')
	OrganizationApiSettings.objects.all().delete()


class Migration(migrations.Migration):

	dependencies = [
		('gregory', '0042_historicalauthors'),
		('organizations', '0001_initial'),
	]

	operations = [
		migrations.CreateModel(
			name='OrganizationApiSettings',
			fields=[
				('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
				('organization', models.OneToOneField(
					help_text='Organisation whose API exposure these settings govern.',
					on_delete=django.db.models.deletion.CASCADE,
					related_name='api_settings',
					to='organizations.organization',
				)),
				('make_api_public', models.BooleanField(
					default=False,
					help_text="When true, anonymous API and RSS consumers can see this org's data.",
				)),
				('created_at', models.DateTimeField(auto_now_add=True)),
				('updated_at', models.DateTimeField(auto_now=True)),
			],
			options={
				'verbose_name': 'Organisation API settings',
				'verbose_name_plural': 'Organisation API settings',
			},
		),
		migrations.RunPython(backfill_api_settings, reverse_code=reverse_backfill),
	]
