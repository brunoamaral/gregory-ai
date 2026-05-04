import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('api', '0002_apiaccessschemelog_payload_received'),
		('organizations', '0001_initial'),
	]

	operations = [
		migrations.AddField(
			model_name='apiaccessscheme',
			name='organization',
			field=models.ForeignKey(
				blank=True,
				help_text='Organisation this key represents. Required after the transition window.',
				null=True,
				on_delete=django.db.models.deletion.CASCADE,
				related_name='api_access_schemes',
				to='organizations.organization',
			),
		),
	]
