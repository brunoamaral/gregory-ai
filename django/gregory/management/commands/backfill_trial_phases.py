"""One-time backfill of Trials.phase_normalized from the existing Trials.phase values.

Trials.save() recomputes phase_normalized from phase on every write (see gregory/models.py),
so this command only matters for rows written before that hook existed, and for rows
touched by bulk_update elsewhere (which bypasses save() entirely). It scans every trial,
recomputes phase_normalized with gregory.utils.trial_field_normalizers.normalize_phase, and
bulk_updates rows whose stored value differs.

Intentionally skips django-simple-history: this is a derived field recomputed from data
already in the row, not a meaningful edit, and tens of thousands of _change_reason entries
would drown out real history. bulk_update also can't populate history since it doesn't call
save().

Idempotent: rerunning recomputes the same values and updates nothing once the DB is caught up.
"""

from collections import Counter

from django.core.management.base import BaseCommand

from gregory.models import Trials
from gregory.utils.trial_field_normalizers import TrialPhase, normalize_phase


class Command(BaseCommand):
	help = "Backfill Trials.phase_normalized from Trials.phase for every trial."

	def add_arguments(self, parser):
		parser.add_argument(
			"--batch-size",
			type=int,
			default=1000,
			help="Rows per bulk_update batch (default: 1000).",
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Report what would change without saving.",
		)

	def handle(self, *args, **options):
		batch_size = max(options["batch_size"], 1)
		dry_run = options["dry_run"]
		verbosity = options.get("verbosity", 1)

		queryset = Trials.objects.only(
			"trial_id", "phase", "phase_normalized"
		).order_by("trial_id")

		scanned = 0
		to_update = []
		tally = Counter()
		other_raw_values = set()

		for trial in queryset.iterator(chunk_size=2000):
			scanned += 1
			new_value = normalize_phase(trial.phase)
			if new_value == trial.phase_normalized:
				continue

			if verbosity >= 2:
				self.stdout.write(
					f"{trial.trial_id}: {trial.phase_normalized!r} -> {new_value!r} "
					f"(raw phase={trial.phase!r})"
				)

			if new_value == TrialPhase.OTHER and trial.phase:
				other_raw_values.add(trial.phase)

			tally[new_value] += 1
			if not dry_run:
				trial.phase_normalized = new_value
				to_update.append(trial)

		updated = 0
		if not dry_run:
			for start in range(0, len(to_update), batch_size):
				batch = to_update[start : start + batch_size]
				Trials.objects.bulk_update(batch, ["phase_normalized"])
				updated += len(batch)
				self.stdout.write(
					f"Updated {updated}/{len(to_update)} trial rows."
				)

		prefix = "Would update" if dry_run else "Updated"
		self.stdout.write(
			self.style.SUCCESS(
				f"Scanned {scanned} trials. {prefix} {sum(tally.values())} rows."
			)
		)

		if tally:
			self.stdout.write("Per-canonical-value tally:")
			for value, count in sorted(tally.items(), key=lambda kv: str(kv[0])):
				self.stdout.write(f"  {value}: {count}")

		if other_raw_values:
			self.stdout.write(
				self.style.WARNING(
					f"{len(other_raw_values)} distinct raw phase value(s) mapped to OTHER "
					"— review and extend the mapping table in "
					"gregory/utils/trial_field_normalizers.py:"
				)
			)
			for raw_value in sorted(other_raw_values):
				self.stdout.write(f"  {raw_value!r}")
