"""Add a type flag to TeamCategory: automatic categories are populated by
rebuild_categories from their term list, manual categories are curated
entirely by hand and skipped by the command. All existing categories are
automatic, which is also the default for new ones.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

	dependencies = [
		('gregory', '0060_category_assignment_provenance'),
	]

	operations = [
		migrations.AddField(
			model_name='teamcategory',
			name='category_type',
			field=models.CharField(
				choices=[('manual', 'Manual'), ('automatic', 'Automatic')],
				default='automatic',
				help_text=(
					'Automatic categories are populated by the rebuild_categories command from the term list '
					'(manual assignments are still allowed and preserved). Manual categories are curated entirely '
					'by hand and are never touched by the command.'
				),
				max_length=10,
			),
		),
	]
