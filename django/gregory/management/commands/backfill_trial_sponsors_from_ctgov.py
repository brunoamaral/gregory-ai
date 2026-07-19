"""One-time backfill of Trials.primary_sponsor / secondary_sponsor / lead_sponsor_class
from the ClinicalTrials.gov API, for trials that predate sponsor-class capture in the
CTGov importer (and, for the ~12.9k legacy pre-CTGov-importer rows, predate sponsor
capture entirely — see TRIALS-SPONSOR-CANONICALIZATION-PLAN.md PR 1 §7).

Clone of backfill_trial_countries' skeleton — same conventions, same idempotent
selection-on-emptiness, same batched filter.ids fetch with retry/backoff, same shared
extraction logic with the live importer (ClinicalTrialsGovAPI.extract_sponsor_fields;
see gregory/classes.py) so this command can never disagree with what
feedreader_trials_ctgov would have written.

Selection: trials with a non-empty NCT identifier AND (empty `primary_sponsor` OR null
`lead_sponsor_class`) — covers both the legacy sponsor-less rows and class-capture for
rows that already have a sponsor name but predate the lead_sponsor_class column.

Write path (per row, only for fields the API actually returned/were empty):
    trial.primary_sponsor = <api value>       # only when trial.primary_sponsor was empty
    trial.secondary_sponsor = <api value>      # only when trial.secondary_sponsor was empty
    trial.lead_sponsor_class = <api value>     # always, when the API returned one
    trial.save()
Unlike backfill_trial_countries (which uses update_fields=[...] to avoid triggering
unrelated recomputation), this command calls a plain trial.save() — it deliberately DOES
need Trials.save()'s full write path here, since that is what resolves
primary_sponsor_normalized (see Trials._resolve_primary_sponsor()) for every
newly-sponsored trial. bulk_update is never used here: it bypasses that resolution.
"""

import re
import time

from django.core.management.base import BaseCommand
from django.db.models import Q

from gregory.classes import ClinicalTrialsGovAPI
from gregory.models import Trials

# Captured at import time (same rationale as backfill_trial_countries): patching
# ClinicalTrialsGovAPI in tests to stub out .search() never affects extraction — it stays
# the real, pure/deterministic logic shared with parse_study_to_clinical_trial.
_extract_sponsor_fields = ClinicalTrialsGovAPI.extract_sponsor_fields

NCT_RE = re.compile(r"^NCT\d{8}$")
FIELDS = [
	"protocolSection.identificationModule.nctId",
	"protocolSection.sponsorCollaboratorsModule",
]
CHANGE_REASON = "Backfilled sponsor fields from ClinicalTrials.gov API"


class Command(BaseCommand):
	help = (
		"Backfill Trials.primary_sponsor / secondary_sponsor / lead_sponsor_class from "
		"the ClinicalTrials.gov API for trials with an NCT identifier and either no "
		"primary_sponsor or no lead_sponsor_class."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--batch-size",
			type=int,
			default=100,
			help="NCT ids per API request (default: 100, max: 1000).",
		)
		parser.add_argument(
			"--limit",
			type=int,
			help="Stop after this many candidate trials (useful for a smoke test).",
		)
		parser.add_argument(
			"--sleep",
			type=float,
			default=0.5,
			help="Seconds to wait between API requests (default: 0.5).",
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Report what would be updated without saving.",
		)

	def handle(self, *args, **options):
		batch_size = min(max(options["batch_size"], 1), 1000)
		limit = options.get("limit")
		sleep = max(options["sleep"], 0)
		dry_run = options["dry_run"]
		verbosity = options.get("verbosity", 1)

		candidates = (
			Trials.objects.filter(identifiers__has_key="nct")
			.filter(
				Q(primary_sponsor__isnull=True)
				| Q(primary_sponsor="")
				| Q(lead_sponsor_class__isnull=True)
			)
			.order_by("trial_id")
		)
		if limit:
			candidates = candidates[:limit]

		trials_by_nct = {}
		invalid = 0
		for trial in candidates:
			nct = (trial.identifiers.get("nct") or "").strip().upper()
			if not NCT_RE.match(nct):
				invalid += 1
				if verbosity >= 2:
					self.stdout.write(
						self.style.WARNING(
							f"Skipping trial {trial.trial_id}: invalid NCT id {trial.identifiers.get('nct')!r}"
						)
					)
				continue
			trials_by_nct.setdefault(nct, []).append(trial)

		nct_ids = sorted(trials_by_nct)
		total = len(nct_ids)
		self.stdout.write(
			f"{total} NCT ids missing sponsor data ({invalid} skipped as invalid)."
		)
		if not total:
			return

		api = ClinicalTrialsGovAPI()
		filled_sponsor = filled_class = not_found = 0
		failed_batches = []

		for start in range(0, total, batch_size):
			batch = nct_ids[start : start + batch_size]
			studies = self._fetch_batch(api, batch, sleep, failed_batches)
			if studies is None:
				continue

			sponsor_by_nct = {}
			for study in studies:
				ident = study.get("protocolSection", {}).get("identificationModule", {})
				nct = (ident.get("nctId") or "").strip().upper()
				if nct:
					sponsor_by_nct[nct] = _extract_sponsor_fields(study)

			for nct in batch:
				if nct not in sponsor_by_nct:
					not_found += 1
					continue
				fields = sponsor_by_nct[nct]

				for trial in trials_by_nct[nct]:
					changed_sponsor, changed_class = self._apply_sponsor_fields(
						trial, fields, dry_run
					)
					filled_sponsor += changed_sponsor
					filled_class += changed_class
					if verbosity >= 2 and (changed_sponsor or changed_class):
						self.stdout.write(f"{nct}: {fields}")

			done = min(start + batch_size, total)
			self.stdout.write(
				f"Processed {done}/{total} NCT ids "
				f"(filled sponsor {filled_sponsor}, class {filled_class})."
			)
			if sleep and done < total:
				time.sleep(sleep)

		prefix = "Would fill" if dry_run else "Filled"
		self.stdout.write(
			self.style.SUCCESS(
				f"{prefix} primary_sponsor on {filled_sponsor} trial row(s), "
				f"lead_sponsor_class on {filled_class} trial row(s). "
				f"Not found on ClinicalTrials.gov: {not_found} NCT ids."
			)
		)
		if failed_batches:
			self.stdout.write(
				self.style.ERROR(
					f"{len(failed_batches)} batch(es) failed and were skipped — rerun to retry: "
					f"{', '.join(failed_batches[:5])}{'…' if len(failed_batches) > 5 else ''}"
				)
			)

	def _apply_sponsor_fields(self, trial, fields, dry_run) -> tuple[bool, bool]:
		"""Write path for one trial. Fill-only-when-empty for primary_sponsor/
		secondary_sponsor (never overwrite a value another source already provided);
		lead_sponsor_class is always set when the API returned one (it is new data no
		other source populates). Returns (sponsor_filled, class_filled) — both False when
		the API had nothing new for this trial. A real (non-bulk) save() is required
		here: it is what resolves primary_sponsor_normalized for a trial gaining a
		sponsor for the first time — see Trials._resolve_primary_sponsor()."""
		changed = False
		changed_sponsor = False
		changed_class = False
		update_fields = []

		new_primary = fields.get("primary_sponsor")
		if new_primary and not trial.primary_sponsor:
			if not dry_run:
				trial.primary_sponsor = new_primary
			changed = changed_sponsor = True
			update_fields.append("primary_sponsor")

		new_secondary = fields.get("secondary_sponsor")
		if new_secondary and not trial.secondary_sponsor:
			if not dry_run:
				trial.secondary_sponsor = new_secondary
			changed = True
			update_fields.append("secondary_sponsor")

		new_class = fields.get("lead_sponsor_class")
		if new_class and trial.lead_sponsor_class != new_class:
			if not dry_run:
				trial.lead_sponsor_class = new_class
			changed = changed_class = True
			update_fields.append("lead_sponsor_class")

		if changed and not dry_run:
			trial._change_reason = CHANGE_REASON
			trial.save(update_fields=update_fields)

		return changed_sponsor, changed_class

	def _fetch_batch(self, api, batch, sleep, failed_batches):
		"""Fetch one filter.ids batch, retrying with exponential backoff before skipping
		it — a batch command shouldn't die 130 requests in because of one transient 502."""
		max_attempts = 3
		for attempt in range(1, max_attempts + 1):
			try:
				response = api.search(
					filter_ids=batch,
					fields=FIELDS,
					page_size=len(batch),
					count_total=False,
				)
				return response.get("studies", [])
			except Exception as exc:
				if attempt < max_attempts:
					backoff = sleep * (2**attempt)
					self.stderr.write(
						self.style.WARNING(
							f"Batch {batch[0]}–{batch[-1]} failed ({exc}); retrying in "
							f"{backoff:.1f}s (attempt {attempt}/{max_attempts})."
						)
					)
					time.sleep(backoff)
				else:
					self.stderr.write(
						self.style.ERROR(
							f"Batch {batch[0]}–{batch[-1]} failed {max_attempts} times "
							f"({exc}); skipping."
						)
					)
					failed_batches.append(f"{batch[0]}–{batch[-1]}")
		return None
