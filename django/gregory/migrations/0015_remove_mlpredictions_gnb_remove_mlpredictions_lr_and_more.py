# Generated by Django 5.2.3 on 2025-06-24 11:41

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('gregory', '0014_alter_sources_keyword_filter'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='mlpredictions',
            name='gnb',
        ),
        migrations.RemoveField(
            model_name='mlpredictions',
            name='lr',
        ),
        migrations.RemoveField(
            model_name='mlpredictions',
            name='lsvc',
        ),
        migrations.RemoveField(
            model_name='mlpredictions',
            name='mnb',
        ),
        migrations.RemoveField(
            model_name='sources',
            name='language',
        ),
    ]
