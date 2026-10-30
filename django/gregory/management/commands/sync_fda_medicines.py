"""
Sync the FDA's medicine approvals into the Medicine table.

Two public files feed it, and each is a half of the run:

* Drugs@FDA (openFDA's bulk file, about 9 MB zipped, refreshed on weekdays): every NDA and
  CDER BLA, with its products and every approved submission. ANDAs (generics) are skipped.
  One Medicine per application number, with events rebuilt from the FDA's dates.
* The Purple Book (a monthly CSV): the CBER biologics, which Drugs@FDA does not list, each
  one Medicine per BLA. For the CDER BLAs it only sets ``biosimilar`` and ``interchangeable``
  on the row Drugs@FDA made.

The first sync reconstructs past approvals and efficacy supplements from the FDA's dates;
later syncs record what they observe. Rows that drop out of their file are flagged
``in_latest_export=False``, never deleted.

Each file is checked before anything is written: it must parse, Drugs@FDA must hold as many
applications as its ``meta.results.total`` says, and neither may have lost more than 5% of its
NDA and BLA applications (Drugs@FDA) or CBER BLAs (Purple Book) since the last sync. The two
halves are independent: a failed check stops that half, writes nothing for it and lets the
other run, and the command then exits with an error.

Run:
	docker exec gregory python manage.py sync_fda_medicines
	docker exec gregory python manage.py sync_fda_medicines --file drugsfda.json.zip --purplebook-file pb.csv --dry-run
"""

import zipfile
from collections import Counter, defaultdict

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from gregory.management.base import GregoryBaseCommand
from gregory.models import FdaSyncState, Medicine, MedicineEvent, Regulator
from gregory.services.fda_events import (
	new_dated_events,
	observed_status_events,
	reconstructed_events,
)
from gregory.utils.fda import (
	application_type,
	download_drugsfda,
	download_purplebook,
	drugsfda_text,
	parse_application,
	parse_purplebook,
	read_drugsfda,
)

BATCH_SIZE = 500
MAX_COUNT_DROP = 0.05
MAX_LISTED_MISSING = 20

CDER = "CDER"
CBER = "CBER"

Source = FdaSyncState.Source

# The dated event kinds a sync writes from submissions. Observed status events are
# not compared, since they are dated the day they were seen.
DATED_KINDS = (
	MedicineEvent.Kind.FDA_APPROVED,
	MedicineEvent.Kind.FDA_TENTATIVE_APPROVAL,
	MedicineEvent.Kind.FDA_EFFICACY_SUPPLEMENT,
)

# Columns each half owns. Drugs@FDA leaves biosimilar and interchangeable to the Purple
# Book, and neither half touches indication, label_indications or label_checked_at, which
# the label sync fills. A CBER row's url is left alone too: DailyMed supplies it.
DRUGSFDA_FIELDS = [
	"fda_application_type", "fda_center", "name", "inn", "active_substance", "substances",
	"is_combination", "status", "pharmacotherapeutic_group", "fda_pharm_classes", "holder",
	"authorisation_date", "brand_names", "fda_original_class", "fda_priority_review", "url",
	"raw", "raw_digest",
]
PURPLEBOOK_FIELDS = [
	"fda_application_type", "fda_center", "name", "inn", "active_substance", "substances",
	"is_combination", "status", "holder", "biosimilar", "interchangeable",
	"authorisation_date", "brand_names", "raw", "raw_digest",
]
# Columns bulk_update writes on a changed row. Rows are loaded without ``raw`` (about 40 MB
# of JSON), so "changed" compares ``raw_digest`` instead; raw is still written when it moves.
BOOKKEEPING = ["in_latest_export", "last_synced_at"]


def compared(fields):
	return [name for name in fields if name != "raw"]


class Command(GregoryBaseCommand):
	help = (
		"Sync the FDA's NDA and BLA approvals (Drugs@FDA, plus CBER biologics from the "
		"Purple Book) and their regulatory events."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--file",
			metavar="PATH",
			help="Read a local Drugs@FDA file (the zip or the JSON) instead of downloading it.",
		)
		parser.add_argument(
			"--purplebook-file",
			metavar="PATH",
			help="Read a local Purple Book CSV instead of downloading it.",
		)
		parser.add_argument(
			"--purplebook-url",
			metavar="URL",
			help="Download this Purple Book CSV instead of the newest one linked on the downloads page.",
		)
		parser.add_argument(
			"--source",
			choices=[Source.DRUGSFDA, Source.PURPLEBOOK],
			help="Run one half only. Without it both run, or only the ones given a local file.",
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Parse, compare and report; write nothing.",
		)
		parser.add_argument(
			"--force",
			action="store_true",
			help="Download even if the file has not changed, and accept a file with fewer applications than the last sync.",
		)

	def handle(self, *args, **options):
		self.dry_run = options["dry_run"]
		self.planned_cder = set()
		# Set when the Drugs@FDA half creates a BLA row, so the Purple Book half has to read
		# its file whatever the server says: that is where the row's biosimilar flag is.
		self.new_bla = False
		halves = self._halves(options)

		failed = False
		wrote = False
		for source in halves:
			run = self._run_drugsfda if source == Source.DRUGSFDA else self._run_purplebook
			try:
				wrote = run(options) or wrote
			except CommandError as exc:
				failed = True
				self.stderr.write(f"{self._label(source)}: {exc}")

		if wrote and not self.dry_run:
			# sync_fda_labels runs here once it exists, before the matcher reads the labels.
			#
			# A sync that changed substances relinks categories in the same run. Outside the
			# transactions: a matching failure must not undo a good sync, and the pipeline's
			# own match_medicines step retries it.
			call_command("match_medicines", stdout=self.stdout, stderr=self.stderr)
		if failed:
			raise CommandError("At least one FDA file failed its checks; see above.")

	@staticmethod
	def _label(source):
		return "Drugs@FDA" if source == Source.DRUGSFDA else "Purple Book"

	def _halves(self, options):
		if options["source"]:
			return [options["source"]]
		given = [
			source
			for source, path in (
				(Source.DRUGSFDA, options["file"]),
				(Source.PURPLEBOOK, options["purplebook_file"]),
			)
			if path
		]
		# A local file for one half means the other half has nothing to read.
		return given or [Source.DRUGSFDA, Source.PURPLEBOOK]

	def _state(self, source):
		return FdaSyncState.objects.filter(source=source).first() or FdaSyncState(source=source)

	# --- Drugs@FDA -----------------------------------------------------

	def _run_drugsfda(self, options):
		"""Returns True when the half read a file (whether or not it wrote)."""
		state = self._state(Source.DRUGSFDA)
		loaded = self._load(
			options["file"], download_drugsfda, state, options["force"], "Drugs@FDA"
		)
		if loaded is None:
			return False
		body, headers = loaded

		parsed_rows, skipped, bad_dates = self._parse_drugsfda(body)
		self._check_count(len(parsed_rows), state, options["force"], "NDA and BLA applications")

		plan = self._plan_drugsfda(parsed_rows)
		self.planned_cder = set(parsed_rows)
		self._report("Drugs@FDA", plan, skipped, bad_dates)
		if self.dry_run:
			return False

		with transaction.atomic():
			self._write(plan)
			self._save_state(state, len(parsed_rows), headers)
		self.new_bla = any(row.fda_application_type == "BLA" for row, _ in plan["new"])
		return True

	def _load(self, path, download, state, force, label):
		"""Return ``(body bytes, response headers)``, or None on a 304."""
		if path:
			try:
				with open(path, "rb") as handle:
					return handle.read(), {}
			except OSError as exc:
				raise CommandError(f"Cannot read {path}: {exc}")
		try:
			status, body, headers = download(None if force else state)
		except Exception as exc:
			raise CommandError(f"Could not download the file: {exc}")
		if status == 304:
			self.stdout.write(f"{label} file unchanged (HTTP 304); nothing to do.")
			return None
		if status != 200:
			raise CommandError(f"The server returned HTTP {status}.")
		return body, headers

	def _parse_drugsfda(self, body):
		"""Parse the NDA and BLA applications. Returns ``(parsed by number, skipped, bad dates)``."""
		try:
			text = drugsfda_text(body)
		except (ValueError, zipfile.BadZipFile) as exc:
			raise CommandError(f"Not a Drugs@FDA file: {exc}")
		try:
			meta, results = read_drugsfda(text)
			parsed_rows = {}
			skipped = Counter()
			bad_dates = Counter()
			seen = 0
			for record in results:
				seen += 1
				if not isinstance(record, dict):
					skipped["malformed"] += 1
					continue
				kind = application_type(record)
				if kind == "ANDA":
					skipped["ANDAs"] += 1
					continue
				if not kind:
					skipped["not an NDA, ANDA or BLA"] += 1
					continue
				parsed = parse_application(record)
				if parsed is None:
					skipped["no application number"] += 1
					continue
				if parsed.fda_application_number in parsed_rows:
					skipped["duplicate application number"] += 1
					continue
				for column in parsed.bad_dates:
					bad_dates[column] += 1
				parsed_rows[parsed.fda_application_number] = parsed
		except ValueError as exc:
			raise CommandError(f"The Drugs@FDA file is not valid JSON: {exc}")
		finally:
			del text

		try:
			expected = int(((meta or {}).get("results") or {}).get("total"))
		except (TypeError, ValueError):
			raise CommandError("The Drugs@FDA file has no usable meta.results.total.")
		if seen != expected:
			raise CommandError(
				f"The file holds {seen} applications but meta.results.total says {expected}; "
				"it looks truncated. Nothing was written."
			)
		return parsed_rows, skipped, bad_dates

	def _check_count(self, count, state, force, what):
		if count == 0:
			raise CommandError(f"The file has no {what}. Nothing was written.")
		previous = state.last_count
		if not force and previous and count < previous * (1 - MAX_COUNT_DROP):
			raise CommandError(
				f"The file has {count} {what}, down from {previous} at the last sync "
				f"(more than {MAX_COUNT_DROP:.0%}). It looks partial. Nothing was written. "
				"Use --force if the drop is real."
			)

	def _stored_events(self):
		"""The (kind, date) pairs of every stored dated FDA event, by medicine id."""
		stored = defaultdict(set)
		rows = MedicineEvent.objects.filter(
			medicine__regulator=Regulator.FDA, kind__in=DATED_KINDS
		).values_list("medicine_id", "kind", "event_date")
		for medicine_id, kind, event_date in rows:
			stored[medicine_id].add((kind, event_date))
		return stored

	def _new_plan(self, fields):
		return {
			"new": [],  # (Medicine, [event dicts])
			"changed": [],  # (Medicine, [event dicts])
			"events": [],  # (Medicine, [event dicts]) of unchanged rows
			"update_fields": fields + BOOKKEEPING,
			"unchanged": 0,
			"gone": [],
			"returned": 0,
			"flags": [],
			"notes": [],
		}

	def _stored_fda_medicines(self):
		# raw is deferred: the digest says whether it changed, and a changed row gets the
		# new raw assigned before it is written.
		return {
			m.fda_application_number: m
			for m in Medicine.objects.filter(regulator=Regulator.FDA).defer("raw")
		}

	def _diff(self, plan, parsed_rows, existing, fields, skip=None):
		"""Compare ``parsed_rows`` with the stored ``existing`` rows, in memory, and fill
		``plan``: new rows with reconstructed events, changed rows (or rows back in the file)
		with their observed events, and unchanged rows (which may still have a new event).

		Events are read off the stored row before it is overwritten. ``skip(medicine)`` leaves
		a stored row alone and counts it; returns that count."""
		stored = self._stored_events()
		today = timezone.localdate()
		now = timezone.now()
		compare = compared(fields)
		skipped = 0
		for number, parsed in parsed_rows.items():
			values = parsed.model_values()
			medicine = existing.get(number)
			if medicine is None:
				row = Medicine(**values, in_latest_export=True, last_synced_at=now)
				plan["new"].append((row, reconstructed_events(parsed)))
				continue
			if skip is not None and skip(medicine):
				skipped += 1
				continue

			events = observed_status_events(medicine, parsed, today) + new_dated_events(
				parsed, stored.get(medicine.pk, set()), today
			)
			changed = any(getattr(medicine, name) != values[name] for name in compare)
			reappeared = not medicine.in_latest_export
			if not changed and not reappeared:
				plan["unchanged"] += 1
				if events:
					plan["events"].append((medicine, events))
				continue
			for name in fields:
				setattr(medicine, name, values[name])
			if reappeared:
				plan["returned"] += 1
			medicine.in_latest_export = True
			medicine.last_synced_at = now
			plan["changed"].append((medicine, events))
		return skipped

	def _plan_drugsfda(self, parsed_rows):
		"""Compare the file with the stored rows, in memory. Nothing is written."""
		existing = self._stored_fda_medicines()
		plan = self._new_plan(DRUGSFDA_FIELDS)
		self._diff(plan, parsed_rows, existing, DRUGSFDA_FIELDS)
		for number, medicine in existing.items():
			if (
				number not in parsed_rows
				and medicine.fda_center != CBER
				and medicine.in_latest_export
			):
				medicine.in_latest_export = False
				plan["gone"].append(medicine)
		return plan

	# --- Purple Book ---------------------------------------------------

	def _run_purplebook(self, options):
		state = self._state(Source.PURPLEBOOK)
		url = options["purplebook_url"]
		loaded = self._load(
			options["purplebook_file"],
			lambda st: download_purplebook(st, url=url),
			state,
			# Conditional headers off, but the count check stays: only --force skips that.
			options["force"] or self.new_bla,
			"Purple Book",
		)
		if loaded is None:
			return False
		body, headers = loaded

		try:
			cber, cder_flags = parse_purplebook(body.decode("utf-8-sig", errors="replace"))
		except ValueError as exc:
			raise CommandError(f"Not a usable Purple Book file: {exc}")
		bad_dates = Counter(column for parsed in cber for column in parsed.bad_dates)
		self._check_count(len(cber), state, options["force"], "CBER BLAs")

		plan = self._plan_purplebook({p.fda_application_number: p for p in cber}, cder_flags)
		self._report("Purple Book", plan, Counter(), bad_dates)
		if self.dry_run:
			return False

		with transaction.atomic():
			self._write(plan)
			self._save_state(state, len(cber), headers, fallback_url=url or "")
		return True

	def _plan_purplebook(self, parsed_rows, cder_flags):
		existing = self._stored_fda_medicines()
		plan = self._new_plan(PURPLEBOOK_FIELDS)
		# Drugs@FDA has the history of a CDER row; the Purple Book must not take it over.
		in_drugsfda = self._diff(
			plan, parsed_rows, existing, PURPLEBOOK_FIELDS, skip=lambda m: m.fda_center != CBER
		)
		for number, medicine in existing.items():
			if number not in parsed_rows and medicine.fda_center == CBER and medicine.in_latest_export:
				medicine.in_latest_export = False
				plan["gone"].append(medicine)

		# CDER BLAs: the Purple Book only says whether a biologic is biosimilar or
		# interchangeable. Setting those two is not an event.
		now = timezone.now()
		matched = 0
		missing = []
		for number, flags in cder_flags.items():
			medicine = existing.get(number)
			if medicine is None or medicine.fda_center != CDER:
				if number in self.planned_cder:
					matched += 1  # a dry run: Drugs@FDA would have created it
				elif medicine is None:
					missing.append(number)
				continue
			matched += 1
			if (medicine.biosimilar, medicine.interchangeable) != (
				flags["biosimilar"],
				flags["interchangeable"],
			):
				medicine.biosimilar = flags["biosimilar"]
				medicine.interchangeable = flags["interchangeable"]
				medicine.last_synced_at = now
				plan["flags"].append(medicine)
		plan["notes"].append(
			f"CDER BLAs: {len(cder_flags)} in the file, {matched} with a Drugs@FDA row, "
			f"{len(plan['flags'])} with biosimilar or interchangeable flags changed."
		)
		if missing:
			listed = ", ".join(missing[:MAX_LISTED_MISSING]) + (
				", …" if len(missing) > MAX_LISTED_MISSING else ""
			)
			plan["notes"].append(
				f"{len(missing)} CDER BLAs are in the Purple Book but not in Drugs@FDA, so they "
				f"were not imported: {listed}."
			)
		if in_drugsfda:
			plan["notes"].append(
				f"{in_drugsfda} CBER BLAs already have a Drugs@FDA row and were left to it."
			)
		return plan

	# --- writing -------------------------------------------------------

	def _write(self, plan):
		new_medicines = [medicine for medicine, _ in plan["new"]]
		# PostgreSQL returns the new primary keys, which the events need.
		Medicine.objects.bulk_create(new_medicines, batch_size=BATCH_SIZE)

		changed_medicines = [medicine for medicine, _ in plan["changed"]]
		Medicine.objects.bulk_update(changed_medicines, plan["update_fields"], batch_size=BATCH_SIZE)
		Medicine.objects.bulk_update(plan["gone"], ["in_latest_export"], batch_size=BATCH_SIZE)
		Medicine.objects.bulk_update(
			plan["flags"],
			["biosimilar", "interchangeable", "last_synced_at"],
			batch_size=BATCH_SIZE,
		)

		# A corrected one-off milestone (the approval date) replaces the event for its old date.
		stale = Q()
		for medicine, medicine_events in plan["changed"] + plan["events"]:
			for event in medicine_events:
				if "replaces" in event:
					stale |= Q(medicine=medicine, kind=event["kind"], event_date__in=event["replaces"])
		if stale:
			MedicineEvent.objects.filter(stale).delete()

		events = [
			MedicineEvent(
				medicine=medicine,
				**{key: value for key, value in event.items() if key != "replaces"},
			)
			for medicine, medicine_events in plan["new"] + plan["changed"] + plan["events"]
			for event in medicine_events
		]
		# ignore_conflicts: the unique constraint makes a repeated run harmless.
		MedicineEvent.objects.bulk_create(events, batch_size=BATCH_SIZE, ignore_conflicts=True)

	def _save_state(self, state, count, headers, fallback_url=""):
		state.last_count = count
		state.last_run_at = timezone.now()
		if headers.get("url") or fallback_url:
			state.source_url = headers.get("url") or fallback_url
		if headers:
			state.last_modified = headers.get("Last-Modified", "")
			state.etag = headers.get("ETag", "")
		state.save()

	# --- reporting -----------------------------------------------------

	def _report(self, label, plan, skipped, bad_dates):
		events = Counter()
		for _, medicine_events in plan["new"] + plan["changed"] + plan["events"]:
			for event in medicine_events:
				events[(event["basis"], event["kind"])] += 1
		prefix = "[dry run] " if self.dry_run else ""
		self.stdout.write(
			f"{prefix}{label}: {len(plan['new'])} new, {len(plan['changed'])} changed "
			f"({plan['returned']} back in the file), {plan['unchanged']} unchanged, "
			f"{len(plan['gone'])} no longer in the file."
		)
		skipped_text = ", ".join(f"{count} {reason}" for reason, count in skipped.items())
		self.stdout.write(f"{prefix}{label} skipped: {skipped_text or 'none'}.")
		for note in plan["notes"]:
			self.stdout.write(f"{prefix}{label}: {note}")
		self.stdout.write(
			f"{prefix}{label} unparseable dates: {sum(bad_dates.values())}"
			+ (f" ({dict(bad_dates)})" if bad_dates else "")
			+ "."
		)
		self.stdout.write(f"{prefix}{label} events: {sum(events.values())}")
		for (basis, kind), count in sorted(events.items()):
			self.stdout.write(f"{prefix}{label}   {kind} ({basis}): {count}")
		if self.dry_run:
			self.stdout.write(f"{label}: dry run, nothing was written.")
