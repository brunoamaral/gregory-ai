"""One-time backfill of Trials normalized fields (phase_normalized,
recruitment_status_normalized, ...) from their raw counterparts.

Trials.save() recomputes every derived field registered in
gregory.utils.trial_field_normalizers.NORMALIZED_TRIAL_FIELDS from its raw counterpart on
every write (see gregory/models.py), so this command only matters for rows written before
that hook existed, and for rows touched by bulk_update elsewhere (which bypasses save()
entirely). It scans every trial, recomputes the selected derived field(s), and
bulk_updates rows whose stored value(s) differ, flushing every --batch-size dirty rows so
peak memory stays bounded by the batch rather than the table.

Intentionally skips django-simple-history: these are derived fields recomputed from data
already in the row, not a meaningful edit, and tens of thousands of _change_reason entries
would drown out real history. bulk_update also can't populate history since it doesn't call
save().

Idempotent: rerunning recomputes the same values and updates nothing once the DB is caught up.

Covers every field registered in NORMALIZED_TRIAL_FIELDS by default; pass --field to scope
a run to one or more of them (e.g. while tuning a single field's mapping table).
"""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from gregory.models import Trials
from gregory.utils.trial_field_normalizers import NORMALIZED_TRIAL_FIELDS

_FIELDS_BY_NAME = {entry[0]: entry for entry in NORMALIZED_TRIAL_FIELDS}


class Command(BaseCommand):
	help = (
		"Backfill Trials normalized fields (phase_normalized, recruitment_status_normalized, "
		"...) from their raw counterparts for every trial."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--field",
			action="append",
			help=(
				"Raw field to backfill: one of "
				f"{', '.join(_FIELDS_BY_NAME)}. Repeatable, or comma-separated "
				"(e.g. --field phase,recruitment_status). Default: all registered fields."
			),
		)
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

	def _resolve_fields(self, field_option):
		"""Return the NORMALIZED_TRIAL_FIELDS entries selected by --field, in registry order.

		--field is unset -> every registered field. Accepts repeated flags and/or
		comma-separated names; raises CommandError on an unknown field name.
		"""
		if not field_option:
			return list(NORMALIZED_TRIAL_FIELDS)

		# call_command(..., field="phase") passes a bare str rather than the ["phase"]
		# argparse's action="append" would build from a real CLI invocation.
		if isinstance(field_option, str):
			field_option = [field_option]

		requested = {
			name.strip()
			for chunk in field_option
			for name in chunk.split(",")
			if name.strip()
		}
		unknown = requested - _FIELDS_BY_NAME.keys()
		if unknown:
			raise CommandError(
				f"Unknown --field value(s): {', '.join(sorted(unknown))}. "
				f"Valid choices: {', '.join(_FIELDS_BY_NAME)}."
			)
		return [entry for entry in NORMALIZED_TRIAL_FIELDS if entry[0] in requested]

	def handle(self, *args, **options):
		batch_size = max(options["batch_size"], 1)
		dry_run = options["dry_run"]
		verbosity = options.get("verbosity", 1)
		fields = self._resolve_fields(options["field"])

		select_fields = ["trial_id"]
		for raw_field, derived_field, _normalizer in fields:
			select_fields += [raw_field, derived_field]

		queryset = Trials.objects.only(*select_fields).order_by("trial_id")

		derived_fields = [derived_field for _, derived_field, _ in fields]
		scanned = 0
		dirty_rows = 0
		updated = 0
		# Dirty rows awaiting the next bulk_update flush. Flushed every --batch-size rows so
		# peak memory stays bounded by the batch, not the table (on the first prod run every
		# row is dirty).
		pending = []
		tally = {raw_field: Counter() for raw_field, _, _ in fields}
		other_raw_values = {raw_field: set() for raw_field, _, _ in fields}

		def flush_pending():
			nonlocal updated
			Trials.objects.bulk_update(pending, derived_fields)
			updated += len(pending)
			self.stdout.write(f"Updated {updated} trial rows so far.")
			pending.clear()

		for trial in queryset.iterator(chunk_size=2000):
			scanned += 1
			dirty = False

			for raw_field, derived_field, normalizer in fields:
				raw_value = getattr(trial, raw_field)
				current_value = getattr(trial, derived_field)
				new_value = normalizer(raw_value)

				if new_value == current_value:
					continue

				dirty = True
				if verbosity >= 2:
					self.stdout.write(
						f"{trial.trial_id}: {derived_field} {current_value!r} -> {new_value!r} "
						f"(raw {raw_field}={raw_value!r})"
					)

				if new_value == "other" and raw_value:
					other_raw_values[raw_field].add(raw_value)

				tally[raw_field][new_value] += 1
				if not dry_run:
					setattr(trial, derived_field, new_value)

			if dirty:
				dirty_rows += 1
				if not dry_run:
					pending.append(trial)
					if len(pending) >= batch_size:
						flush_pending()

		if not dry_run and pending:
			flush_pending()

		field_names = ", ".join(raw_field for raw_field, _, _ in fields)
		prefix = "Would update" if dry_run else "Updated"
		self.stdout.write(
			self.style.SUCCESS(
				f"Scanned {scanned} trials for field(s): {field_names}. "
				f"{prefix} {dirty_rows} rows."
			)
		)

		for raw_field, derived_field, _normalizer in fields:
			field_tally = tally[raw_field]
			if field_tally:
				self.stdout.write(f"Per-canonical-value tally for {derived_field}:")
				for value, count in sorted(field_tally.items(), key=lambda kv: str(kv[0])):
					self.stdout.write(f"  {value}: {count}")

			raw_values = other_raw_values[raw_field]
			if raw_values:
				self.stdout.write(
					self.style.WARNING(
						f"{len(raw_values)} distinct raw {raw_field} value(s) mapped to OTHER "
						"— review and extend the mapping table in "
						"gregory/utils/trial_field_normalizers.py:"
					)
				)
				for raw_value in sorted(raw_values):
					self.stdout.write(f"  {raw_value!r}")
