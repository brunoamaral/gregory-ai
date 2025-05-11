from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('gregory', '0037_add_subject_slug'),  # Make sure this matches your latest migration
    ]

    operations = [
        migrations.CreateModel(
            name='PredictionRunLog',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('model_version', models.CharField(help_text='Version identifier for the model used', max_length=100)),
                ('run_type', models.CharField(choices=[('train', 'Training'), ('predict', 'Prediction')], help_text='Type of run: training or prediction', max_length=10)),
                ('run_started', models.DateTimeField(auto_now_add=True, help_text='When the run was started')),
                ('run_finished', models.DateTimeField(blank=True, help_text='When the run was completed', null=True)),
                ('success', models.BooleanField(blank=True, help_text='Whether the run was successful', null=True)),
                ('triggered_by', models.CharField(blank=True, help_text='User or system that triggered the run', max_length=100, null=True)),
                ('error_message', models.TextField(blank=True, help_text='Error message if the run failed', null=True)),
                ('subject', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='prediction_run_logs', to='gregory.subject')),
                ('team', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='prediction_run_logs', to='gregory.team')),
            ],
            options={
                'verbose_name': 'Prediction Run Log',
                'verbose_name_plural': 'Prediction Run Logs',
                'indexes': [
                    models.Index(fields=['team', 'subject', 'run_finished'], name='gregory_pre_team_id_40de51_idx'),
                    models.Index(fields=['run_type', 'success'], name='gregory_pre_run_typ_e57299_idx'),
                ],
            },
        ),
    ]
