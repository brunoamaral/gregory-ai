"""Add per-category matching settings to TeamCategory.

- ``match_scope``: search/score the title only, or the title and summary
  (and, for trials, the full set of searchable fields).
- ``match_min_score``: per-category score threshold, replacing the previous
  global ``--min-score`` argument on rebuild_categories.
- ``match_weights``: per-field score weights keyed by content type.

Defaults reproduce the historical hard-coded scoring so existing categories
keep behaving exactly as before until someone edits them.
"""
from django.db import migrations, models
import gregory.models


class Migration(migrations.Migration):

	dependencies = [
		("gregory", "0066_remove_historicalarticles_crossref_retraction_check"),
	]

	operations = [
		migrations.AddField(
			model_name="teamcategory",
			name="match_scope",
			field=models.CharField(
				choices=[
					("title", "Title only"),
					("title_summary", "Title and summary"),
				],
				default="title_summary",
				help_text=(
					"Which fields are searched and scored when matching content to this "
					"category. 'Title only' scores just the title; 'Title and summary' also "
					"scores the summary (and, for trials, the scientific title, intervention, "
					"outcomes and therapeutic areas)."
				),
				max_length=20,
			),
		),
		migrations.AddField(
			model_name="teamcategory",
			name="match_min_score",
			field=models.PositiveSmallIntegerField(
				default=3,
				help_text=(
					"Minimum score an article or trial must reach to be assigned to this "
					"category."
				),
			),
		),
		migrations.AddField(
			model_name="teamcategory",
			name="match_weights",
			field=models.JSONField(
				blank=True,
				default=gregory.models.default_match_weights,
				help_text=(
					"Per-field score weights keyed by content type ('article', 'trial'). "
					"Higher weights make a matched field count for more. A fixed bonus of 2 "
					"points per unique matched term is always added."
				),
			),
		),
	]
