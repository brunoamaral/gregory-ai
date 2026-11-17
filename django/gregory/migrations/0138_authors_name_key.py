from django.db import migrations, models

from gregory.utils.author_names import name_key

BATCH = 5000


def backfill_name_key(apps, schema_editor):
	Authors = apps.get_model("gregory", "Authors")
	pending = []
	rows = Authors.objects.only("author_id", "given_name", "family_name", "name_key").iterator(
		chunk_size=BATCH
	)
	for author in rows:
		key = name_key(author.given_name, author.family_name)
		if key != author.name_key:
			author.name_key = key
			pending.append(author)
		if len(pending) >= BATCH:
			Authors.objects.bulk_update(pending, ["name_key"])
			pending = []
	if pending:
		Authors.objects.bulk_update(pending, ["name_key"])


class Migration(migrations.Migration):

	dependencies = [
		("gregory", "0137_labs"),
	]

	operations = [
		migrations.AddField(
			model_name="authors",
			name="name_key",
			field=models.CharField(
				blank=True,
				db_index=True,
				default="",
				editable=False,
				help_text="Normalised 'given|family' key (accents, case, dots and hyphens ignored) used by the importers to match spelling variants. Set by save().",
				max_length=310,
			),
		),
		migrations.RunPython(backfill_name_key, migrations.RunPython.noop),
	]
