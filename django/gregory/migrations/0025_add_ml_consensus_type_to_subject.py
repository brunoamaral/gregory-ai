# Generated manually for ML consensus type feature

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gregory', '0024_remove_deprecated_relevant_field'),
    ]

    operations = [
        migrations.AddField(
            model_name='subject',
            name='ml_consensus_type',
            field=models.CharField(
                choices=[
                    ('any', 'Any Model (at least one predicts relevant)'),
                    ('majority', 'Majority Vote (at least 2 out of 3 agree)'),
                    ('all', 'Unanimous (all models must agree)')
                ],
                default='any',
                help_text='How ML models should agree for an article to be considered relevant',
                max_length=10
            ),
        ),
    ]
