"""Rename the `team_categories` M2M fields to `categories`.

Articles, Trials and Patents each link to TeamCategory through an explicit
through model with its own db_table, so the M2M field has no column and the
renames change no tables. Team's reverse accessor (`team.team_categories`)
becomes `team.categories`, and the admin labels the model "categories".
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		("gregory", "0120_keyword_filter_wildcard_help"),
	]

	operations = [
		migrations.RenameField(
			model_name="articles",
			old_name="team_categories",
			new_name="categories",
		),
		migrations.RenameField(
			model_name="trials",
			old_name="team_categories",
			new_name="categories",
		),
		migrations.RenameField(
			model_name="patents",
			old_name="team_categories",
			new_name="categories",
		),
		migrations.AlterField(
			model_name="teamcategory",
			name="team",
			field=models.ForeignKey(
				on_delete=django.db.models.deletion.CASCADE,
				related_name="categories",
				to="gregory.team",
			),
		),
		migrations.AlterModelOptions(
			name="teamcategory",
			options={"verbose_name": "category", "verbose_name_plural": "categories"},
		),
		migrations.AlterModelOptions(
			name="historicalteamcategory",
			options={
				"get_latest_by": ("history_date", "history_id"),
				"ordering": ("-history_date", "-history_id"),
				"verbose_name": "historical category",
				"verbose_name_plural": "historical categories",
			},
		),
	]
