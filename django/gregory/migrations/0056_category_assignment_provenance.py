"""Convert the implicit team_categories M2M through tables into explicit
through models carrying a `source` flag (manual/automatic).

The CreateModel/AlterField operations are state-only: the through models map
onto the tables Django auto-created for the implicit M2Ms, so no tables are
created or renamed. The only real schema change is adding the `source` column.

Existing rows are backfilled as 'automatic' (they were overwhelmingly written
by rebuild_categories, and the next run re-asserts any that still match), then
the default is flipped to 'manual' so every assignment made outside the
rebuild command — admin, API, shell — is protected from automatic removal.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

	dependencies = [
		('gregory', '0055_trials_registry_identifier_indexes'),
	]

	operations = [
		migrations.SeparateDatabaseAndState(
			state_operations=[
				migrations.CreateModel(
					name='ArticleCategoryAssignment',
					fields=[
						('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
						('articles', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='category_assignments', to='gregory.articles')),
						('teamcategory', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='article_assignments', to='gregory.teamcategory')),
					],
					options={
						'db_table': 'articles_team_categories',
						'verbose_name': 'article category assignment',
						'unique_together': {('articles', 'teamcategory')},
					},
				),
				migrations.CreateModel(
					name='TrialCategoryAssignment',
					fields=[
						('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
						('trials', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='category_assignments', to='gregory.trials')),
						('teamcategory', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='trial_assignments', to='gregory.teamcategory')),
					],
					options={
						'db_table': 'trials_team_categories',
						'verbose_name': 'trial category assignment',
						'unique_together': {('trials', 'teamcategory')},
					},
				),
				migrations.AlterField(
					model_name='articles',
					name='team_categories',
					field=models.ManyToManyField(blank=True, related_name='articles', through='gregory.ArticleCategoryAssignment', to='gregory.teamcategory'),
				),
				migrations.AlterField(
					model_name='trials',
					name='team_categories',
					field=models.ManyToManyField(related_name='trials', through='gregory.TrialCategoryAssignment', to='gregory.teamcategory'),
				),
			],
			database_operations=[],
		),
		migrations.AddField(
			model_name='articlecategoryassignment',
			name='source',
			field=models.CharField(choices=[('manual', 'Manual'), ('automatic', 'Automatic')], default='automatic', max_length=10),
			preserve_default=False,
		),
		migrations.AddField(
			model_name='trialcategoryassignment',
			name='source',
			field=models.CharField(choices=[('manual', 'Manual'), ('automatic', 'Automatic')], default='automatic', max_length=10),
			preserve_default=False,
		),
		migrations.AlterField(
			model_name='articlecategoryassignment',
			name='source',
			field=models.CharField(choices=[('manual', 'Manual'), ('automatic', 'Automatic')], default='manual', max_length=10),
		),
		migrations.AlterField(
			model_name='trialcategoryassignment',
			name='source',
			field=models.CharField(choices=[('manual', 'Manual'), ('automatic', 'Automatic')], default='manual', max_length=10),
		),
	]
