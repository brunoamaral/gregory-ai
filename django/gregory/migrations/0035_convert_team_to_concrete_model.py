from django.db import migrations, models
import django.db.models.deletion
from django.utils.text import slugify


def create_teams_from_organizations(apps, schema_editor):
    """
    Convert existing proxy Team models to concrete Team models
    with a OneToOne relationship to their parent Organization.
    """
    Organization = apps.get_model('organizations', 'Organization')
    Team = apps.get_model('gregory', 'Team')
    
    # Track used slugs to ensure uniqueness
    used_slugs = set()
    
    # Process each organization and create corresponding team
    for org in Organization.objects.all():
        base_slug = slugify(org.name)
        slug = base_slug
        
        # Handle slug collisions by appending numbers
        counter = 1
        while slug in used_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1
        
        used_slugs.add(slug)
        
        # Create the concrete Team model linked to this Organization
        Team.objects.create(
            organization=org,
            slug=slug
        )


def reverse_team_migration(apps, schema_editor):
    """
    Remove all concrete Team instances.
    Since Teams were proxy models before, we just need to drop the concrete instances.
    """
    Team = apps.get_model('gregory', 'Team')
    Team.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('organizations', '0001_initial'),  # Organizations app dependency
        ('gregory', '0034_alter_teamcategory_subjects'),  # Make sure we depend on the latest gregory migration
    ]

    operations = [
        # First, remove the proxy model definition
        migrations.DeleteModel(
            name='Team',
        ),
        
        # Create new concrete Team model with a OneToOne field to Organization
        migrations.CreateModel(
            name='Team',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.SlugField(unique=True, editable=True)),
                ('organization', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='team', to='organizations.Organization')),
            ],
        ),
        
        # Run the data migration to create Team instances for each Organization
        migrations.RunPython(
            create_teams_from_organizations,
            reverse_team_migration,
        ),
    ]
