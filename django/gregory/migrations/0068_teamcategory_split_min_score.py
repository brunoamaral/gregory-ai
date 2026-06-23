"""Split TeamCategory.match_min_score into two per-content-type thresholds.

Articles are scored on up to 2 fields (title, summary); trials on up to 7
(title, summary, scientific title, intervention, primary/secondary outcome,
therapeutic areas).  A single shared threshold can't serve both well, so we
replace it with match_min_score_articles and match_min_score_trials.

The data migration copies the existing value into both new fields so every
existing category retains exactly its current behaviour after the upgrade.
"""
from django.db import migrations, models


def copy_min_score(apps, schema_editor):
	TeamCategory = apps.get_model("gregory", "TeamCategory")
	TeamCategory.objects.update(
		match_min_score_articles=models.F("match_min_score"),
		match_min_score_trials=models.F("match_min_score"),
	)


class Migration(migrations.Migration):

	dependencies = [
		("gregory", "0067_teamcategory_match_settings"),
	]

	operations = [
		migrations.AddField(
			model_name="teamcategory",
			name="match_min_score_articles",
			field=models.PositiveSmallIntegerField(
				default=3,
				help_text=(
					"Minimum score an article must reach to be assigned to this category. "
					"Articles are scored on up to 2 fields (title, summary)."
				),
			),
		),
		migrations.AddField(
			model_name="teamcategory",
			name="match_min_score_trials",
			field=models.PositiveSmallIntegerField(
				default=3,
				help_text=(
					"Minimum score a trial must reach to be assigned to this category. "
					"Trials are scored on up to 7 fields (title, summary, scientific title, "
					"intervention, primary outcome, secondary outcome, therapeutic areas)."
				),
			),
		),
		migrations.RunPython(copy_min_score, reverse_code=migrations.RunPython.noop),
		migrations.RemoveField(
			model_name="teamcategory",
			name="match_min_score",
		),
	]
