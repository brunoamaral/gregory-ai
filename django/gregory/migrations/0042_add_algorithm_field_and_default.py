from django.db import migrations, models
# undelete?
class Migration(migrations.Migration):

    dependencies = [
	('gregory', '0038_extend_ml_predictions'),
    ]

    operations = [
        migrations.AddField(
            model_name='predictionrunlog',
            name='algorithm',
            field=models.CharField(
                choices=[
                    ('pubmed_bert', 'PubMed BERT'),
                    ('lgbm_tfidf', 'LGBM TF-IDF'),
                    ('lstm', 'LSTM'),
                    ('unknown', 'Unknown')
                ],
                default='unknown',
                help_text='ML algorithm used for the run',
                max_length=20
            ),
        ),
    ]

    def apply_default_algorithm(apps, schema_editor):
        PredictionRunLog = apps.get_model('gregory', 'PredictionRunLog')
        for log in PredictionRunLog.objects.all():
            log.algorithm = 'unknown'
            log.save(update_fields=['algorithm'])

    operations.append(
        migrations.RunPython(
            apply_default_algorithm,
            reverse_code=migrations.RunPython.noop
        )
    )
