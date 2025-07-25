# Generated by Django 5.1.5 on 2025-06-14 21:24

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gregory', '0011_alter_sources_language'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sources',
            name='keyword_filter',
            field=models.TextField(blank=True, help_text='Keywords to filter articles. Use comma-separated values for multiple keywords, or quoted strings for exact phrases (e.g., "multiple sclerosis", alzheimer, parkinson). Only applies to certain feed sources like bioRxiv.', null=True),
        ),
        migrations.CreateModel(
            name='ArticleTrialReference',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('identifier_type', models.CharField(help_text="Which identifier was found (e.g., 'nct_id', 'isrctn')", max_length=50)),
                ('identifier_value', models.CharField(help_text='The actual identifier value', max_length=100)),
                ('discovered_date', models.DateTimeField(auto_now_add=True)),
                ('article', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='trial_references', to='gregory.articles')),
                ('trial', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='article_references', to='gregory.trials')),
            ],
            options={
                'verbose_name_plural': 'article trial references',
                'db_table': 'article_trial_references',
                'indexes': [models.Index(fields=['identifier_type', 'identifier_value'], name='article_tri_identif_21da30_idx')],
                'unique_together': {('article', 'trial', 'identifier_type')},
            },
        ),
    ]
