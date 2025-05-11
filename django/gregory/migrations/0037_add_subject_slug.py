from django.db import migrations, models
from django.utils.text import slugify


def populate_subject_slugs(apps, schema_editor):
    """
    Auto-populate the subject_slug field for existing subjects.
    Ensures slugs are unique per team by adding numeric suffixes when needed.
    """
    Subject = apps.get_model('gregory', 'Subject')
    
    # Group subjects by team for handling uniqueness within teams
    team_subjects = {}
    
    for subject in Subject.objects.all():
        if subject.team_id not in team_subjects:
            team_subjects[subject.team_id] = []
        team_subjects[subject.team_id].append(subject)
    
    # Process each team's subjects
    for team_id, subjects in team_subjects.items():
        used_slugs = set()
        
        for subject in subjects:
            # Generate base slug
            base_slug = slugify(subject.subject_name)
            slug = base_slug
            
            # Handle collisions within this team
            counter = 1
            while slug in used_slugs:
                slug = f"{base_slug}-{counter}"
                counter += 1
            
            used_slugs.add(slug)
            subject.subject_slug = slug
            subject.save()


def reverse_migration(apps, schema_editor):
    """
    No need to do anything since we're just removing a field.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        # This should point to your latest migration, update if necessary
        ('gregory', '0036_convert_team_to_concrete_model'),
    ]

    operations = [
        # Add the subject_slug field
        migrations.AddField(
            model_name='subject',
            name='subject_slug',
            field=models.SlugField(default='', editable=True),
            preserve_default=False,
        ),
        
        # Run the data migration to populate slugs
        migrations.RunPython(
            populate_subject_slugs,
            reverse_migration
        ),
        
        # Add the unique_together constraint
        migrations.AlterUniqueTogether(
            name='subject',
            unique_together={('team', 'subject_slug')},
        ),
    ]
