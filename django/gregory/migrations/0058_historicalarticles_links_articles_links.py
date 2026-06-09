from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		("gregory", "0057_backfill_trials_links"),
	]

	operations = [
		migrations.AddField(
			model_name="historicalarticles",
			name="links",
			field=models.JSONField(
				blank=True,
				help_text='All known URLs for this article, keyed by source domain. Managed automatically. Corresponds to "links" in the API response.',
				null=True,
			),
		),
		migrations.AddField(
			model_name="articles",
			name="links",
			field=models.JSONField(
				blank=True,
				help_text='All known URLs for this article, keyed by source domain. Managed automatically. Corresponds to "links" in the API response.',
				null=True,
			),
		),
	]
