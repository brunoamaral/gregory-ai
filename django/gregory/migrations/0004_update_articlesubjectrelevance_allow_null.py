# Generated by Django 5.1.5 on 2025-06-01 20:02

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('gregory', '0003_add_auto_predict_to_subject'),
    ]

    operations = [
        migrations.AlterField(
            model_name='articlesubjectrelevance',
            name='is_relevant',
            field=models.BooleanField(blank=True, default=None, help_text='Indicates if the article is relevant for the subject. NULL means not reviewed.', null=True),
        ),
    ]
