"""Rename the TeamCategory permission labels to match its new verbose name.

0121 changed TeamCategory's verbose name to "category", but Django only
creates missing permissions (by codename) and never renames existing ones, so
the permission picker kept showing "Can change team category". Codenames are
unchanged, so groups and code that grant them are unaffected.
"""

from django.db import migrations

MODEL_NAMES = {
	"teamcategory": ("team category", "category"),
	"historicalteamcategory": ("historical team category", "historical category"),
}


def _relabel(apps, use_new):
	Permission = apps.get_model("auth", "Permission")
	for model, (old, new) in MODEL_NAMES.items():
		label = new if use_new else old
		perms = Permission.objects.filter(
			content_type__app_label="gregory", content_type__model=model
		)
		for perm in perms:
			action = perm.codename.split("_", 1)[0]
			perm.name = f"Can {action} {label}"
			perm.save(update_fields=["name"])


def forwards(apps, schema_editor):
	_relabel(apps, use_new=True)


def backwards(apps, schema_editor):
	_relabel(apps, use_new=False)


class Migration(migrations.Migration):

	dependencies = [
		("gregory", "0121_rename_team_categories_to_categories"),
		("auth", "0012_alter_user_first_name_max_length"),
		("contenttypes", "0002_remove_content_type_name"),
	]

	operations = [
		migrations.RunPython(forwards, backwards),
	]
