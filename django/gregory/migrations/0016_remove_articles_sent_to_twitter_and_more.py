# Generated by Django 4.0.4 on 2022-08-05 18:29

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('gregory', '0015_sources_ignore_ssl'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='articles',
            name='sent_to_twitter',
        ),
        migrations.RemoveField(
            model_name='trials',
            name='sent_to_twitter',
        ),
    ]