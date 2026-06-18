from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		("gregory", "0064_articles_crossref_retraction_check_and_more"),
	]

	operations = [
		migrations.AddField(
			model_name="articles",
			name="ml_score",
			field=models.FloatField(
				blank=True,
				db_index=True,
				default=None,
				help_text="Average ML probability score across the latest prediction per (algorithm, subject) pair. Updated automatically when predictions are saved.",
				null=True,
			),
		),
	]
