"""One-time backfill of Trials.primary_completion_date / completion_date /
completion_date_type from the ClinicalTrials.gov API, for trials that predate completion
date capture in the CTGov importer.

Same skeleton as backfill_trial_secondary_ids_from_ctgov: batched filter.ids fetch with
retry/backoff, and extraction shared with the live importer
(ClinicalTrialsGovAPI.parse_study_to_clinical_trial) so this command can never disagree
with what feedreader_trials_ctgov would have written.

Selection: trials with an NCT identifier AND no primary_completion_date AND no
completion_date. A trial whose registry record carries no completion dates is selected
again on every run; that is cheap and keeps the command stateless.

Write path: save(update_fields=[...]) on only the three completion fields."""

import re
import time

from django.core.management.base import BaseCommand

from gregory.classes import ClinicalTrialsGovAPI
from gregory.models import Trials

# Captured at import time so patching ClinicalTrialsGovAPI in tests (to stub .search())
# never affects the shared extraction logic.
_extract_completion_dates = ClinicalTrialsGovAPI.extract_completion_dates

NCT_RE = re.compile(r"^NCT\d{8}$")
FIELDS = [
	"protocolSection.identificationModule.nctId",
	"protocolSection.statusModule.primaryCompletionDateStruct",
	"protocolSection.statusModule.completionDateStruct",
]
CHANGE_REASON = "Backfilled completion dates from ClinicalTrials.gov API"


def extract_completion_dates(study):
	"""Return (primary_completion_date, completion_date, completion_date_type) for one
	API study, using the same rules as the live importer."""
	status = (study.get("protocolSection") or {}).get("statusModule") or {}
	return _extract_completion_dates(status)


class Command(BaseCommand):
	help = (
		"Backfill Trials.primary_completion_date / completion_date from the "
		"ClinicalTrials.gov API for NCT trials that have neither."
	)

	def add_arguments(self, parser):
		parser.add_argument("--batch-size", type=int, default=100, help="NCT ids per API request (default: 100, max: 1000).")
		parser.add_argument("--limit", type=int, help="Stop after this many candidate trials.")
		parser.add_argument("--sleep", type=float, default=0.5, help="Seconds between API requests (default: 0.5).")
		parser.add_argument("--dry-run", action="store_true", help="Report what would be updated without saving.")

	def handle(self, *args, **options):
		batch_size = min(max(options["batch_size"], 1), 1000)
		limit = options.get("limit")
		sleep = max(options["sleep"], 0)
		dry_run = options["dry_run"]

		candidates = (
			Trials.objects.filter(identifiers__has_key="nct")
			.filter(primary_completion_date__isnull=True, completion_date__isnull=True)
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
				continue
			trials_by_nct.setdefault(nct, []).append(trial)

		nct_ids = sorted(trials_by_nct)
		total = len(nct_ids)
		self.stdout.write(f"{total} NCT ids missing completion dates ({invalid} skipped as invalid).")
		if not total:
			return

		api = ClinicalTrialsGovAPI()
		filled = 0
		without_dates = 0
		failed_batches = []

		for start in range(0, total, batch_size):
			batch = nct_ids[start : start + batch_size]
			studies = self._fetch_batch(api, batch, sleep, failed_batches)
			if studies is None:
				continue

			dates_by_nct = {}
			for study in studies:
				ident = (study.get("protocolSection") or {}).get("identificationModule") or {}
				nct = (ident.get("nctId") or "").strip().upper()
				if nct:
					dates_by_nct[nct] = extract_completion_dates(study)

			for nct in batch:
				primary, completion, date_type = dates_by_nct.get(nct, (None, None, None))
				if not (primary or completion):
					without_dates += 1
					continue
				for trial in trials_by_nct[nct]:
					filled += 1
					if not dry_run:
						trial.primary_completion_date = primary
						trial.completion_date = completion
						trial.completion_date_type = date_type
						trial._change_reason = CHANGE_REASON
						trial.save(
							update_fields=[
								"primary_completion_date",
								"completion_date",
								"completion_date_type",
							]
						)

			done = min(start + batch_size, total)
			self.stdout.write(f"Processed {done}/{total} NCT ids (filled {filled}).")
			if sleep and done < total:
				time.sleep(sleep)

		prefix = "Would fill" if dry_run else "Filled"
		self.stdout.write(
			self.style.SUCCESS(
				f"{prefix} completion dates on {filled} trial row(s). "
				f"{without_dates} NCT ids have no completion date on ClinicalTrials.gov (or were not returned)."
			)
		)
		if failed_batches:
			self.stdout.write(
				self.style.ERROR(
					f"{len(failed_batches)} batch(es) failed and were skipped, rerun to retry: "
					f"{', '.join(failed_batches[:5])}"
				)
			)

	def _fetch_batch(self, api, batch, sleep, failed_batches):
		"""Fetch one filter.ids batch, retrying with exponential backoff before skipping it."""
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
							f"Batch {batch[0]}-{batch[-1]} failed ({exc}); retrying in {backoff:.1f}s "
							f"(attempt {attempt}/{max_attempts})."
						)
					)
					time.sleep(backoff)
				else:
					self.stderr.write(
						self.style.ERROR(f"Batch {batch[0]}-{batch[-1]} failed {max_attempts} times ({exc}); skipping.")
					)
					failed_batches.append(f"{batch[0]}-{batch[-1]}")
		return None
