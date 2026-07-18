"""One-time backfill of Trials normalized fields (phase_normalized,
recruitment_status_normalized, regions_normalized, ...) from their raw counterparts, plus
the TrialCountry rows derived from the same raw country columns.

Trials.save() recomputes every derived field registered in
gregory.utils.trial_field_normalizers.NORMALIZED_TRIAL_FIELDS from its raw counterpart(s) on
every write (see gregory/models.py), and also replaces the trial's TrialCountry rows via
Trials.sync_trial_countries(). So this command only matters for rows written before those
hooks existed, and for rows touched by bulk_update elsewhere (which bypasses save() and
sync_trial_countries() entirely). It scans every trial, recomputes the selected derived
field(s) (and, when the "regions" field is selected, rebuilds TrialCountry rows too), and
bulk_updates rows whose stored scalar value(s) differ, flushing every --batch-size dirty
rows so peak memory stays bounded by the batch rather than the table.

Intentionally skips django-simple-history: these are derived fields recomputed from data
already in the row, not a meaningful edit, and tens of thousands of _change_reason entries
would drown out real history. bulk_update also can't populate history since it doesn't call
save().

Idempotent: rerunning recomputes the same values and updates nothing once the DB is caught up
(TrialCountry sync included — see Trials.sync_trial_countries()).

Covers every field registered in NORMALIZED_TRIAL_FIELDS by default; pass --field to scope
a run to one or more of them (e.g. while tuning a single field's mapping table). Selector
names are the derived field name with its "_normalized" suffix dropped: "phase",
"recruitment_status", "regions" (the countries/TrialCountry layer — see
docs/trials-field-normalization.md).

Do NOT run this against a live/production database as part of this change — it is being
shipped ahead of the one-time prod backfill run, which happens separately once the migration
has been deployed.
"""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from gregory.models import Trials
from gregory.utils.trial_field_normalizers import NORMALIZED_TRIAL_FIELDS, raw_field_names


def _field_key(entry) -> str:
	"""User-facing --field selector name for a NORMALIZED_TRIAL_FIELDS entry: the derived
	field name with its "_normalized" suffix dropped (e.g. "phase_normalized" -> "phase",
	"regions_normalized" -> "regions")."""
	_raw_fields, derived_field, _normalizer = entry
	suffix = "_normalized"
	return derived_field[: -len(suffix)] if derived_field.endswith(suffix) else derived_field


_FIELDS_BY_NAME = {_field_key(entry): entry for entry in NORMALIZED_TRIAL_FIELDS}


class Command(BaseCommand):
	help = (
		"Backfill Trials normalized fields (phase_normalized, recruitment_status_normalized, "
		"regions_normalized, ...) from their raw counterparts for every trial, and rebuild "
		"TrialCountry rows from the raw country columns."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--field",
			action="append",
			help=(
				"Field to backfill: one of "
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
		return [entry for entry in NORMALIZED_TRIAL_FIELDS if _field_key(entry) in requested]

	def handle(self, *args, **options):
		batch_size = max(options["batch_size"], 1)
		dry_run = options["dry_run"]
		verbosity = options.get("verbosity", 1)
		fields = self._resolve_fields(options["field"])
		sync_countries = any(_field_key(entry) == "regions" for entry in fields)

		select_fields = ["trial_id"]
		for raw_fields, derived_field, _normalizer in fields:
			select_fields += [*raw_field_names(raw_fields), derived_field]

		queryset = Trials.objects.only(*select_fields).order_by("trial_id")

		derived_fields = [derived_field for _, derived_field, _ in fields]
		scanned = 0
		dirty_rows = 0
		updated = 0
		countries_synced = 0
		# Dirty rows awaiting the next bulk_update flush. Flushed every --batch-size rows so
		# peak memory stays bounded by the batch, not the table (on the first prod run every
		# row is dirty).
		pending = []
		field_keys = [_field_key(entry) for entry in fields]
		tally = {key: Counter() for key in field_keys}
		other_raw_values = {key: set() for key in field_keys}

		def flush_pending():
			nonlocal updated
			Trials.objects.bulk_update(pending, derived_fields)
			updated += len(pending)
			self.stdout.write(f"Updated {updated} trial rows so far.")
			pending.clear()

		for trial in queryset.iterator(chunk_size=2000):
			scanned += 1
			dirty = False

			for (raw_fields, derived_field, normalizer), key in zip(fields, field_keys):
				names = raw_field_names(raw_fields)
				raw_values = tuple(getattr(trial, name) for name in names)
				current_value = getattr(trial, derived_field)
				new_value = normalizer(*raw_values)

				if new_value == current_value:
					continue

				dirty = True
				if verbosity >= 2:
					raw_repr = ", ".join(
						f"{name}={value!r}" for name, value in zip(names, raw_values)
					)
					self.stdout.write(
						f"{trial.trial_id}: {derived_field} {current_value!r} -> {new_value!r} "
						f"(raw {raw_repr})"
					)

				if new_value == "other" and raw_values and raw_values[0]:
					other_raw_values[key].add(raw_values[0])

				tally[key][
					tuple(new_value) if isinstance(new_value, list) else new_value
				] += 1
				if not dry_run:
					setattr(trial, derived_field, new_value)

			if dirty:
				dirty_rows += 1
				if not dry_run:
					pending.append(trial)
					if len(pending) >= batch_size:
						flush_pending()

			# The per-country TrialCountry rows live on a related model, so bulk_update
			# above can't cover them — sync every scanned
			# trial explicitly whenever the "regions" field is selected, regardless of
			# whether regions_normalized itself changed (a trial can need fresh
			# TrialCountry rows on the very first backfill run even when its computed
			# regions_normalized happens to already match, e.g. both are None).
			if sync_countries and not dry_run:
				trial.sync_trial_countries()
				countries_synced += 1

		if not dry_run and pending:
			flush_pending()

		field_names = ", ".join(field_keys)
		prefix = "Would update" if dry_run else "Updated"
		self.stdout.write(
			self.style.SUCCESS(
				f"Scanned {scanned} trials for field(s): {field_names}. "
				f"{prefix} {dirty_rows} rows."
			)
		)
		if sync_countries:
			if dry_run:
				self.stdout.write(
					"Dry run: TrialCountry rows were not synced (pass without --dry-run)."
				)
			else:
				self.stdout.write(f"Synced TrialCountry rows for {countries_synced} trials.")

		for key in field_keys:
			field_tally = tally[key]
			if field_tally:
				self.stdout.write(f"Per-canonical-value tally for {key}:")
				for value, count in sorted(field_tally.items(), key=lambda kv: str(kv[0])):
					self.stdout.write(f"  {value}: {count}")

			raw_values = other_raw_values[key]
			if raw_values:
				self.stdout.write(
					self.style.WARNING(
						f"{len(raw_values)} distinct raw {key} value(s) mapped to OTHER "
						"— review and extend the mapping table in "
						"gregory/utils/trial_field_normalizers.py:"
					)
				)
				for raw_value in sorted(raw_values):
					self.stdout.write(f"  {raw_value!r}")
