# Two branches each added a 0135: this joins them. No schema change.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('gregory', '0135_article_trial_reference_confirmed'),
        ('gregory', '0135_openalex_authorships'),
    ]

    operations = []
