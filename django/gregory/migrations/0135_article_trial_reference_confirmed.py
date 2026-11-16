from django.db import migrations, models


def confirm_manual_links(apps, schema_editor):
	"""A link an editor added is vouched for from the start."""
	Reference = apps.get_model("gregory", "ArticleTrialReference")
	Reference.objects.filter(source="manual").update(confirmed=True)


class Migration(migrations.Migration):

	dependencies = [
		("gregory", "0134_normalise_trial_reference_types"),
	]

	operations = [
		migrations.AddField(
			model_name="articletrialreference",
			name="confirmed",
			field=models.BooleanField(
				default=False,
				help_text="Set when a person vouches for the link: an editor added it, or checked an auto-detected one. Detection never sets it, and --reset keeps confirmed rows.",
			),
		),
		migrations.AddField(
			model_name="historicalarticletrialreference",
			name="confirmed",
			field=models.BooleanField(
				default=False,
				help_text="Set when a person vouches for the link: an editor added it, or checked an auto-detected one. Detection never sets it, and --reset keeps confirmed rows.",
			),
		),
		migrations.RunPython(confirm_manual_links, migrations.RunPython.noop),
	]
