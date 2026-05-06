import django.db.models.deletion
from django.db import migrations, models


def check_no_null_organizations(apps, schema_editor):
	"""Abort the migration if any APIAccessScheme row still has organization=None."""
	APIAccessScheme = apps.get_model('api', 'APIAccessScheme')
	null_count = APIAccessScheme.objects.filter(organization__isnull=True).count()
	if null_count > 0:
		raise Exception(
			f'Migration aborted: {null_count} APIAccessScheme row(s) have organization=None. '
			'Backfill the organization field for all rows before running this migration.'
		)


class Migration(migrations.Migration):

	dependencies = [
		('api', '0003_apiaccessscheme_organization'),
		('organizations', '0001_initial'),
	]

	operations = [
		# Pre-check: fail fast if any row still lacks an organisation.
		migrations.RunPython(
			check_no_null_organizations,
			reverse_code=migrations.RunPython.noop,
		),
		# Drop null=True / blank=True from the column.
		migrations.AlterField(
			model_name='apiaccessscheme',
			name='organization',
			field=models.ForeignKey(
				help_text='Organisation this key represents.',
				on_delete=django.db.models.deletion.CASCADE,
				related_name='api_access_schemes',
				to='organizations.organization',
			),
		),
	]
