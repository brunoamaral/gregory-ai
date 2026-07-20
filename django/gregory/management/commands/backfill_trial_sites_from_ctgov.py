"""One-time backfill of TrialSite rows (source="ctgov") from the
ClinicalTrials.gov API, for trials that predate site capture in the CTGov
importer (see TRIAL-GEOGRAPHY-PLAN.md PR G2).

Site extraction is shared with the live importer via
ClinicalTrialsGovAPI.extract_sites (gregory/classes.py) and the write path is
shared via gregory.utils.trial_site_sync.replace_trial_sites (also used by
CTIS site capture, scoped per-source so this command can never delete
"ctis"-sourced rows), so this command can never disagree with what
feedreader_trials_ctgov would have written.

Selection: trials with a non-empty NCT identifier and no "ctgov"-sourced
TrialSite rows yet. Selecting purely on the absence of ctgov sites makes
re-runs idempotent: the command never touches a trial it has already
populated, and an interrupted run can simply be started again.

Deliberately never calls trial.save() — sites don't affect any derived trial
field (unlike countries, which recompute TrialCountry/regions_normalized), so
writing only via replace_trial_sites avoids ~12.8k pointless saves.
"""

import re
import time

from django.core.management.base import BaseCommand

from gregory.classes import ClinicalTrialsGovAPI
from gregory.models import Trials
from gregory.utils.trial_site_sync import replace_trial_sites

# Captured at import time so patching ClinicalTrialsGovAPI (e.g. in tests, to stub out
# network calls made by .search()) never affects extraction — it stays the real,
# pure/deterministic logic shared with the live importer.
_extract_sites = ClinicalTrialsGovAPI.extract_sites

NCT_RE = re.compile(r"^NCT\d{8}$")
# Dot-path field selectors, matching backfill_trial_countries' convention. Requesting
# the whole contactsLocationsModule guarantees the nested structure extract_sites()
# reads (facility/city/state/zip/country/geoPoint).
FIELDS = [
	"protocolSection.identificationModule.nctId",
	"protocolSection.contactsLocationsModule",
]


class Command(BaseCommand):
	help = (
		"Backfill TrialSite rows (source='ctgov') from the ClinicalTrials.gov API "
		"for trials with an NCT identifier and no ctgov-sourced sites yet."
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
			help="Report projected counts without writing anything.",
		)

	def handle(self, *args, **options):
		batch_size = min(max(options["batch_size"], 1), 1000)
		limit = options.get("limit")
		sleep = max(options["sleep"], 0)
		dry_run = options["dry_run"]
		verbosity = options.get("verbosity", 1)

		candidates = (
			Trials.objects.filter(identifiers__has_key="nct")
			.exclude(trial_sites__sources__contains=["ctgov"])
			.order_by("trial_id")
		)
		if limit:
			candidates = candidates[:limit]

		# NCT ids carry a per-registry unique index, but normalise and map defensively:
		# stray casing/whitespace can still slip in via manual edits or older imports.
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
			f"{total} NCT ids with no ctgov-sourced sites yet ({invalid} skipped as invalid)."
		)
		if not total:
			return

		api = ClinicalTrialsGovAPI()
		trials_processed = sites_created = trials_with_zero_locations = not_found = 0
		failed_batches = []

		for start in range(0, total, batch_size):
			batch = nct_ids[start : start + batch_size]
			studies = self._fetch_batch(api, batch, sleep, failed_batches)
			if studies is None:
				continue

			sites_by_nct = {}
			for study in studies:
				ident = study.get("protocolSection", {}).get("identificationModule", {})
				nct = (ident.get("nctId") or "").strip().upper()
				if nct:
					sites_by_nct[nct] = _extract_sites(study)

			for nct in batch:
				if nct not in sites_by_nct:
					not_found += 1
					continue
				sites = sites_by_nct[nct]
				if not sites:
					trials_with_zero_locations += len(trials_by_nct[nct])

				for trial in trials_by_nct[nct]:
					if not dry_run:
						replace_trial_sites(trial, "ctgov", sites)
					trials_processed += 1
					sites_created += len(sites)
					if verbosity >= 2:
						self.stdout.write(f"{nct}: {len(sites)} site(s)")

			done = min(start + batch_size, total)
			self.stdout.write(
				f"Processed {done}/{total} NCT ids "
				f"({trials_processed} trial rows, {sites_created} sites so far)."
			)
			if sleep and done < total:
				time.sleep(sleep)

		prefix = "Would create" if dry_run else "Created"
		self.stdout.write(
			self.style.SUCCESS(
				f"{prefix} {sites_created} TrialSite rows across {trials_processed} trials. "
				f"No locations on registry: {trials_with_zero_locations} trials. "
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
