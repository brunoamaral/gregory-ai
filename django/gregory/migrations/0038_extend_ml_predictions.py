from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('gregory', '0037_add_prediction_run_log'),
    ]

    operations = [
        migrations.AddField(
            model_name='mlpredictions',
            name='article',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='ml_predictions_detail', to='gregory.articles'),
        ),
        migrations.AddField(
            model_name='mlpredictions',
            name='model_version',
            field=models.CharField(blank=True, help_text='Version identifier of the ML model used', max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='mlpredictions',
            name='probability_score',
            field=models.FloatField(blank=True, help_text='Probability score from the ML model prediction', null=True),
        ),
        migrations.AddField(
            model_name='mlpredictions',
            name='predicted_relevant',
            field=models.BooleanField(blank=True, help_text='Whether the ML model predicted this article as relevant', null=True),
        ),
        migrations.AlterUniqueTogether(
            name='mlpredictions',
            unique_together={('article', 'subject', 'model_version')},
        ),
    ]
