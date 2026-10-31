"""
Sync EMA's centrally authorised human medicines into the Medicine table.

Downloads EMA's public JSON export (about 6.8 MB, refreshed twice a day), upserts one
Medicine per EMA product number and records what changed as MedicineEvent rows. The
first sync reconstructs past events from EMA's date fields; later syncs record what
they observe. Rows that drop out of the export are flagged ``in_latest_export=False``,
never deleted. Veterinary rows are skipped.

The export is checked before anything is written: it must parse, hold as many rows as
its ``meta.total_records`` says, and not have lost more than 5% of its human rows since
the last sync. A failed check raises and changes nothing.

Run:
	docker exec gregory python manage.py sync_ema_medicines
	docker exec gregory python manage.py sync_ema_medicines --file export.json --dry-run
"""

import json
from collections import Counter

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from gregory.management.base import GregoryBaseCommand
from gregory.models import EmaSyncState, Medicine, MedicineEvent, Regulator
from gregory.services.ema_events import observed_events, reconstructed_events
from gregory.utils.ema import download_export, is_human, parse_row
from gregory.utils.substance_key import standalone_keys, substance_key

BATCH_SIZE = 500
MAX_HUMAN_DROP = 0.05

# The columns bulk_update writes on a changed row (everything parsed but the key).
UPDATE_FIELDS = [
	"name", "inn", "active_substance", "substances", "substance_key", "is_combination", "status",
	"opinion_status", "therapeutic_areas", "atc_code", "pharmacotherapeutic_group",
	"indication", "holder", "advanced_therapy", "orphan_medicine",
	"conditional_approval", "exceptional_circumstances", "prime_priority_medicine",
	"accelerated_assessment", "additional_monitoring", "biosimilar", "generic",
	"patient_safety", "rolling_review_start_date", "evaluation_start_date",
	"opinion_date", "authorisation_date", "latest_ec_decision_date", "refusal_date",
	"application_withdrawal_date", "suspension_date", "end_date", "latest_procedure",
	"revision_number", "ema_first_published", "ema_last_updated", "url", "raw",
	"in_latest_export", "last_synced_at",
]
# Columns that decide whether a row counts as changed (last_synced_at never does).
# substance_key is derived from substances by rules in code, so a change to those rules is
# not a change in EMA's data: refresh_substance_keys carries it to unchanged rows, and a row
# the sync writes anyway gets the current key.
COMPARE_FIELDS = [
	f for f in UPDATE_FIELDS if f not in ("last_synced_at", "in_latest_export", "substance_key")
]


class Command(GregoryBaseCommand):
	help = "Sync EMA's centrally authorised human medicines and their regulatory events."

	def add_arguments(self, parser):
		parser.add_argument(
			"--file",
			metavar="PATH",
			help="Read a local export instead of downloading it (skips If-Modified-Since).",
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Parse, compare and report; write nothing.",
		)
		parser.add_argument(
			"--force",
			action="store_true",
			help="Download even if EMA's Last-Modified has not moved.",
		)

	def handle(self, *args, **options):
		dry_run = options["dry_run"]
		state = EmaSyncState.objects.filter(pk=1).first() or EmaSyncState(pk=1)

		payload, last_modified = self._load(options, state)
		if payload is None:
			return

		meta, data = self._validate(payload)
		parsed_rows, skipped, bad_dates = self._parse(data)
		self._check_human_count(len(parsed_rows), state)

		plan = self._plan(parsed_rows)
		self._report(plan, skipped, bad_dates, dry_run)
		if dry_run:
			return

		now = timezone.now()
		with transaction.atomic():
			self._write(plan)
			state.last_human_count = len(parsed_rows)
			state.last_export_timestamp = str(meta.get("timestamp") or "")[:50]
			state.last_run_at = now
			if last_modified:
				state.last_modified = last_modified
			state.save()

		# A sync that changed substances or areas relinks categories in the same run.
		# Outside the transaction: a matching failure must not undo a good sync, and the
		# pipeline's own match_medicines step retries it.
		call_command("match_medicines", stdout=self.stdout, stderr=self.stderr)

	# --- input ---------------------------------------------------------

	def _load(self, options, state):
		"""Return ``(parsed JSON, Last-Modified)``, or ``(None, "")`` on a 304."""
		if options["file"]:
			try:
				with open(options["file"], "rb") as handle:
					body = handle.read()
			except OSError as exc:
				raise CommandError(f"Cannot read {options['file']}: {exc}")
			last_modified = ""
		else:
			since = "" if options["force"] else state.last_modified
			try:
				status, body, last_modified = download_export(since)
			except Exception as exc:
				raise CommandError(f"Could not download EMA's export: {exc}")
			if status == 304:
				self.stdout.write(
					f"EMA export unchanged since {since} (HTTP 304); nothing to do."
				)
				return None, ""
			if status != 200:
				raise CommandError(f"EMA's export returned HTTP {status}.")
		try:
			return json.loads(body), last_modified
		except ValueError as exc:
			raise CommandError(f"EMA's export is not valid JSON: {exc}")

	def _validate(self, payload):
		if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
			raise CommandError("EMA's export has no 'data' list.")
		meta = payload.get("meta") or {}
		try:
			expected = int(meta.get("total_records"))
		except (TypeError, ValueError):
			raise CommandError("EMA's export has no usable meta.total_records.")
		if len(payload["data"]) != expected:
			raise CommandError(
				f"EMA's export holds {len(payload['data'])} rows but meta.total_records "
				f"says {expected}; it looks truncated. Nothing was written."
			)
		return meta, payload["data"]

	def _parse(self, data):
		"""Parse the human rows. Returns ``(parsed by product number, skipped, bad dates)``."""
		parsed_rows = {}
		skipped = Counter()
		bad_dates = Counter()
		for row in data:
			if not isinstance(row, dict):
				skipped["malformed"] += 1
				continue
			if not is_human(row):
				skipped["veterinary"] += 1
				continue
			parsed = parse_row(row)
			if not parsed.ema_product_number:
				skipped["no product number"] += 1
				continue
			if parsed.ema_product_number in parsed_rows:
				skipped["duplicate product number"] += 1
				continue
			for column in parsed.bad_dates:
				bad_dates[column] += 1
			parsed_rows[parsed.ema_product_number] = parsed
		return parsed_rows, skipped, bad_dates

	def _check_human_count(self, human_count, state):
		if human_count == 0:
			raise CommandError("EMA's export has no human medicines. Nothing was written.")
		previous = state.last_human_count
		if previous and human_count < previous * (1 - MAX_HUMAN_DROP):
			raise CommandError(
				f"EMA's export has {human_count} human medicines, down from {previous} "
				f"at the last sync (more than {MAX_HUMAN_DROP:.0%}). It looks partial. "
				"Nothing was written."
			)

	# --- comparing -----------------------------------------------------

	def _plan(self, parsed_rows):
		"""Compare the export with the stored rows, in memory. Nothing is written."""
		# FDA rows share the table but are not in EMA's export; they must not be flagged gone.
		existing = {
			m.ema_product_number: m for m in Medicine.objects.filter(regulator=Regulator.EMA)
		}
		today = timezone.localdate()
		now = timezone.now()

		plan = {
			"new": [],  # (Medicine, [event dicts])
			"changed": [],  # (Medicine, [event dicts])
			"unchanged": 0,
			"gone": [],
			"returned": 0,
		}
		for number, parsed in parsed_rows.items():
			values = parsed.model_values()
			medicine = existing.get(number)
			if medicine is not None:
				values["substance_key"] = substance_key(parsed.substances, pk=medicine.pk)
			if medicine is None:
				row = Medicine(**values, in_latest_export=True, last_synced_at=now)
				plan["new"].append((row, reconstructed_events(parsed)))
				continue

			# Events are read off the stored row before it is overwritten.
			events = observed_events(medicine, parsed, today)
			changed = any(getattr(medicine, name) != values[name] for name in COMPARE_FIELDS)
			reappeared = not medicine.in_latest_export
			if not changed and not reappeared:
				plan["unchanged"] += 1
				continue
			for name in COMPARE_FIELDS:
				setattr(medicine, name, values[name])
			medicine.substance_key = values["substance_key"]
			if reappeared:
				plan["returned"] += 1
			medicine.in_latest_export = True
			medicine.last_synced_at = now
			plan["changed"].append((medicine, events))

		for number, medicine in existing.items():
			if number not in parsed_rows and medicine.in_latest_export:
				medicine.in_latest_export = False
				plan["gone"].append(medicine)
		return plan

	# --- writing -------------------------------------------------------

	def _write(self, plan):
		new_medicines = [medicine for medicine, _ in plan["new"]]
		# PostgreSQL returns the new primary keys, which the events need.
		Medicine.objects.bulk_create(new_medicines, batch_size=BATCH_SIZE)
		# A new row with no substances is keyed by its id, which exists only now.
		Medicine.objects.bulk_update(
			standalone_keys(new_medicines), ["substance_key"], batch_size=BATCH_SIZE
		)

		changed_medicines = [medicine for medicine, _ in plan["changed"]]
		Medicine.objects.bulk_update(changed_medicines, UPDATE_FIELDS, batch_size=BATCH_SIZE)
		Medicine.objects.bulk_update(plan["gone"], ["in_latest_export"], batch_size=BATCH_SIZE)

		# A corrected one-off milestone replaces the event for its old date.
		stale = Q()
		for medicine, medicine_events in plan["changed"]:
			for event in medicine_events:
				if "replaces" in event:
					stale |= Q(medicine=medicine, kind=event["kind"], event_date=event["replaces"])
		if stale:
			MedicineEvent.objects.filter(stale).delete()

		events = [
			MedicineEvent(
				medicine=medicine,
				**{key: value for key, value in event.items() if key != "replaces"},
			)
			for medicine, medicine_events in plan["new"] + plan["changed"]
			for event in medicine_events
		]
		# ignore_conflicts: the unique constraint makes a repeated run harmless.
		MedicineEvent.objects.bulk_create(events, batch_size=BATCH_SIZE, ignore_conflicts=True)

	# --- reporting -----------------------------------------------------

	def _report(self, plan, skipped, bad_dates, dry_run):
		events = Counter()
		for _, medicine_events in plan["new"] + plan["changed"]:
			for event in medicine_events:
				events[(event["basis"], event["kind"])] += 1
		prefix = "[dry run] " if dry_run else ""
		self.stdout.write(
			f"{prefix}Medicines: {len(plan['new'])} new, {len(plan['changed'])} changed "
			f"({plan['returned']} back in the export), {plan['unchanged']} unchanged, "
			f"{len(plan['gone'])} no longer in the export."
		)
		skipped_text = ", ".join(f"{count} {reason}" for reason, count in skipped.items())
		self.stdout.write(f"{prefix}Skipped: {skipped_text or 'none'}.")
		self.stdout.write(
			f"{prefix}Unparseable dates: {sum(bad_dates.values())}"
			+ (f" ({dict(bad_dates)})" if bad_dates else "")
			+ "."
		)
		self.stdout.write(f"{prefix}Events: {sum(events.values())}")
		for (basis, kind), count in sorted(events.items()):
			self.stdout.write(f"{prefix}  {kind} ({basis}): {count}")
		if dry_run:
			self.stdout.write("Dry run: nothing was written.")
