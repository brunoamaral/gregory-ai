"""
Recompute every medicine's substance_key and write the rows where it changed.

The syncs set the key on a new or changed row, but they skip a row whose source data has
not changed, so a change to the rules in gregory/utils/substance_key.py (a new salt word, a
new exception) would never reach the rows already stored. This command does. The pipeline
runs it right after the medicine syncs: it reads about 10k rows, compares in memory and
writes only the differences, so on an ordinary run it writes nothing.

It does not touch last_synced_at and creates no MedicineEvent: the key is derived, not
something a regulator published.

Run:
	docker exec gregory python manage.py refresh_substance_keys --dry-run
	docker exec gregory python manage.py refresh_substance_keys
"""

from django.db import transaction

from gregory.management.base import GregoryBaseCommand
from gregory.models import Medicine
from gregory.utils.substance_key import substance_key

BATCH_SIZE = 500
MAX_SAMPLES = 10


class Command(GregoryBaseCommand):
	help = "Recompute Medicine.substance_key for every row and write the rows that changed."

	def add_arguments(self, parser):
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Report what would change; write nothing.",
		)

	def handle(self, *args, **options):
		dry_run = options["dry_run"]
		total = 0
		changed = []
		samples = []
		# only(): the raw JSON of every row is tens of megabytes and is not needed.
		queryset = Medicine.objects.only("id", "regulator", "substances", "substance_key")
		for medicine in queryset.order_by("pk").iterator(chunk_size=BATCH_SIZE):
			total += 1
			key = substance_key(medicine.substances, medicine.regulator, medicine.pk)
			if key == medicine.substance_key:
				continue
			if len(samples) < MAX_SAMPLES:
				samples.append((medicine.pk, medicine.substance_key, key))
			medicine.substance_key = key
			changed.append(medicine)

		prefix = "[dry run] " if dry_run else ""
		self.stdout.write(
			f"{prefix}Medicines: {total} checked, {len(changed)} substance keys "
			f"{'would change' if dry_run else 'changed'}, {total - len(changed)} already current."
		)
		for pk, old, new in samples:
			self.stdout.write(f"{prefix}  #{pk}: {old!r} -> {new!r}")
		if len(changed) > len(samples):
			self.stdout.write(f"{prefix}  ... and {len(changed) - len(samples)} more.")

		if dry_run or not changed:
			if dry_run:
				self.stdout.write("Dry run: nothing was written.")
			return
		with transaction.atomic():
			Medicine.objects.bulk_update(changed, ["substance_key"], batch_size=BATCH_SIZE)
