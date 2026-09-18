"""One-time backfill of Trials.ctg_secondary_ids from the ClinicalTrials.gov API, for
trials that predate typed-secondary-id capture in the CTGov importer (and, for the
~12.9k legacy pre-CTGov-importer rows, predate it entirely).

Clone of backfill_trial_sponsors_from_ctgov's skeleton — same conventions, same
idempotent selection-on-emptiness, same batched filter.ids fetch with retry/backoff,
same shared extraction logic with the live importer
(ClinicalTrialsGovAPI.extract_secondary_ids; see gregory/classes.py) so this command
can never disagree with what feedreader_trials_ctgov would have written.

Selection: trials with a non-empty NCT identifier AND ctg_secondary_ids IS NULL.
Idempotent: a trial the API lists no secondary ids for gets [] — distinct from NULL
("never fetched") — and is not selected again on a rerun.

Write path (per row): trial.ctg_secondary_ids = <api value>  (a list, possibly empty)
                       trial.save(update_fields=["ctg_secondary_ids"])
A real (non-bulk) save() with update_fields is required here: NORMALIZED_TRIAL_FIELDS
recomputes identifiers_normalized in the same save because "ctg_secondary_ids" is one
of its raw inputs (see Trials.save() / gregory/utils/trial_field_normalizers.py)."""

import re
import time

from django.core.management.base import BaseCommand

from gregory.classes import ClinicalTrialsGovAPI
from gregory.models import Trials

# Captured at import time (same rationale as backfill_trial_sponsors_from_ctgov):
# patching ClinicalTrialsGovAPI in tests to stub out .search() never affects
# extraction — it stays the real, pure/deterministic logic shared with
# parse_study_to_clinical_trial.
_extract_secondary_ids = ClinicalTrialsGovAPI.extract_secondary_ids

NCT_RE = re.compile(r"^NCT\d{8}$")
FIELDS = [
	"protocolSection.identificationModule.nctId",
	"protocolSection.identificationModule.secondaryIdInfos",
]
CHANGE_REASON = "Backfilled ctg_secondary_ids from ClinicalTrials.gov API"


class Command(BaseCommand):
	help = (
		"Backfill Trials.ctg_secondary_ids from the ClinicalTrials.gov API for trials "
		"with an NCT identifier and ctg_secondary_ids still NULL (never fetched)."
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
			.filter(ctg_secondary_ids__isnull=True)
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
			f"{total} NCT ids missing ctg_secondary_ids ({invalid} skipped as invalid)."
		)
		if not total:
			return

		api = ClinicalTrialsGovAPI()
		filled = 0
		not_found_ncts = []
		failed_batches = []

		for start in range(0, total, batch_size):
			batch = nct_ids[start : start + batch_size]
			studies = self._fetch_batch(api, batch, sleep, failed_batches)
			if studies is None:
				continue

			secondary_ids_by_nct = {}
			for study in studies:
				ident = study.get("protocolSection", {}).get("identificationModule", {})
				nct = (ident.get("nctId") or "").strip().upper()
				if nct:
					_secondary_id_text, ctg_secondary_ids = _extract_secondary_ids(ident)
					secondary_ids_by_nct[nct] = ctg_secondary_ids

			for nct in batch:
				if nct not in secondary_ids_by_nct:
					not_found_ncts.append(nct)
					continue
				ctg_secondary_ids = secondary_ids_by_nct[nct]

				for trial in trials_by_nct[nct]:
					changed = self._apply_ctg_secondary_ids(trial, ctg_secondary_ids, dry_run)
					filled += changed
					if verbosity >= 2 and changed:
						self.stdout.write(
							f"{nct}: {len(ctg_secondary_ids)} secondary id(s)"
						)

			done = min(start + batch_size, total)
			self.stdout.write(
				f"Processed {done}/{total} NCT ids (filled ctg_secondary_ids {filled})."
			)
			if sleep and done < total:
				time.sleep(sleep)

		prefix = "Would fill" if dry_run else "Filled"
		self.stdout.write(
			self.style.SUCCESS(
				f"{prefix} ctg_secondary_ids on {filled} trial row(s). "
				f"Not found on ClinicalTrials.gov: {len(not_found_ncts)} NCT ids."
			)
		)
		if not_found_ncts:
			self.stdout.write(
				self.style.WARNING(
					"NCT ids ClinicalTrials.gov did not return (withdrawn/merged/typo'd?), "
					"left NULL — will be retried on the next run: "
					+ ", ".join(not_found_ncts[:20])
					+ ("…" if len(not_found_ncts) > 20 else "")
				)
			)
		if failed_batches:
			self.stdout.write(
				self.style.ERROR(
					f"{len(failed_batches)} batch(es) failed and were skipped — rerun to retry: "
					f"{', '.join(failed_batches[:5])}{'…' if len(failed_batches) > 5 else ''}"
				)
			)

	def _apply_ctg_secondary_ids(self, trial, ctg_secondary_ids, dry_run) -> bool:
		"""Write path for one trial. Selection already guarantees
		trial.ctg_secondary_ids is NULL, so any list the API returns (including
		[]) is a real change; the differs-check is defensive rather than load-
		bearing. Unlike the sponsors backfill this is single-source data (only
		ClinicalTrials.gov ever populates it), so — like primary_sponsor_normalized
		there — save(update_fields=[...]) is what resolves identifiers_normalized
		here, via Trials.save()'s NORMALIZED_TRIAL_FIELDS loop."""
		if ctg_secondary_ids == trial.ctg_secondary_ids:
			return False
		if not dry_run:
			trial.ctg_secondary_ids = ctg_secondary_ids
			trial._change_reason = CHANGE_REASON
			trial.save(update_fields=["ctg_secondary_ids"])
		return True

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
