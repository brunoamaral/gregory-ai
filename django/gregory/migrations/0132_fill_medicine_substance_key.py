from django.db import migrations

from gregory.utils.substance_key import substance_key

BATCH_SIZE = 500


def fill_keys(apps, schema_editor):
	"""Give every medicine its substance_key.

	The key rules live in gregory.utils.substance_key and may change later; the
	refresh_substance_keys command (run by the pipeline) brings every row up to date with
	whatever the rules are then. This fills the column once so the API is right at deploy."""
	Medicine = apps.get_model("gregory", "Medicine")
	batch = []
	# only() keeps the 40 MB of raw JSON out of memory.
	for medicine in Medicine.objects.only("id", "regulator", "substances", "substance_key").iterator(
		chunk_size=BATCH_SIZE
	):
		key = substance_key(medicine.substances, medicine.regulator, medicine.pk)
		if key != medicine.substance_key:
			medicine.substance_key = key
			batch.append(medicine)
		if len(batch) >= BATCH_SIZE:
			Medicine.objects.bulk_update(batch, ["substance_key"], batch_size=BATCH_SIZE)
			batch = []
	if batch:
		Medicine.objects.bulk_update(batch, ["substance_key"], batch_size=BATCH_SIZE)


class Migration(migrations.Migration):

	dependencies = [
		("gregory", "0131_medicine_substance_key"),
	]

	operations = [
		migrations.RunPython(fill_keys, migrations.RunPython.noop),
	]
