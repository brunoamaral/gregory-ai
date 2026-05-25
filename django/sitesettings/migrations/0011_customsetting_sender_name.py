from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sitesettings', '0010_copy_allowed_domains_from_lists'),
    ]

    operations = [
        migrations.AddField(
            model_name='customsetting',
            name='sender_name',
            field=models.CharField(
                blank=True,
                default='',
                help_text="Display name shown in the email From header (e.g. 'Gregory AI'). Leave blank to fall back to the site title.",
                max_length=100,
            ),
        ),
    ]
