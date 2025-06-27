# Remyelination Categories Import Script

This script allows you to import a predefined set of remyelination therapy categories into the Gregory system.

## Categories Included

The script includes 21 remyelination therapy categories, each with:
- Category name (e.g., "Clemastine fumarate")
- Description including type and status (e.g., "Antihistamine (M1 antagonist) - Phase II")
- Search terms for automatic article categorization

## Usage

Run the script from the Django project root using the management command:

```bash
# First, list available teams
python manage.py import_remyelination_categories

# Then, list available subjects for your chosen team
python manage.py import_remyelination_categories --team_id=YOUR_TEAM_ID

# Preview what would be imported (dry run)
python manage.py import_remyelination_categories --team_id=YOUR_TEAM_ID --subject_id=YOUR_SUBJECT_ID --dry_run

# Perform the actual import
python manage.py import_remyelination_categories --team_id=YOUR_TEAM_ID --subject_id=YOUR_SUBJECT_ID
```

## Example

```bash
# List teams
python manage.py import_remyelination_categories

# List subjects for team ID 1
python manage.py import_remyelination_categories --team_id=1

# Import categories for team ID 1 and subject ID 3
python manage.py import_remyelination_categories --team_id=1 --subject_id=3
```

## Notes

- The script will create new categories or update existing ones based on the category slug
- Each category will be associated with the specified subject
- After import, categories will be available in the admin interface at `/admin/gregory/teamcategory/`
