"""One-time backfill of Trials.countries (+ countries_by_source["ctgov"]) from the
ClinicalTrials.gov API, for trials that predate country capture in the CTGov importer.

Phase 1 of TRIAL-COUNTRY-BACKFILL-PLAN.md (repo root): of ~29.7k trials, ~14.1k have no
country data anywhere. 97.7% of those carry an NCT identifier and can be re-fetched
directly from ClinicalTrials.gov's bulk `filter.ids` search — see the plan's "Verified:
ClinicalTrials.gov API covers the bulk" section. Country extraction itself is shared with
the live importer via ClinicalTrialsGovAPI.extract_countries (gregory/classes.py), so this
command can never disagree with what feedreader_trials_ctgov would have written.

Selection: trials with a non-empty NCT identifier and literally no country data from any
source — no TrialCountry rows, empty `countries`, empty `countries_by_source`, empty
`country_status`. Selecting purely on emptiness makes re-runs idempotent: the command
never touches a trial that already has data from any source (CTGov, WHO ICTRP, or EU
CTIS), and an interrupted run can simply be started again.

Deliberately minimal: unlike feedreader_trials_ctgov.update_existing_trial, this command
reads/writes only Trials.countries / countries_by_source["ctgov"] — no other field.

Write path (only when the extracted country string is non-empty):
    trial.countries_by_source = merge_countries_by_source(trial.countries_by_source, "ctgov", countries_str)
    trial.countries = countries_str
    trial.save()
Trials.save() recomputes TrialCountry rows and regions_normalized from the raw country
columns (see gregory/models.py). bulk_update is never used here — it bypasses save() and
therefore that recomputation; see the warning in docs/trials-multi-source-merge.md.
"""

import re
import time

from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from gregory.classes import ClinicalTrialsGovAPI
from gregory.models import Trials
from gregory.utils.registry_utils import merge_countries_by_source
from gregory.utils.trial_field_normalizers import _map_token, _tokenize_countries_value

# Captured at import time so patching ClinicalTrialsGovAPI (e.g. in tests, to stub out
# network calls made by .search()) never affects extraction — it stays the real,
# pure/deterministic logic shared with parse_study_to_clinical_trial.
_extract_countries = ClinicalTrialsGovAPI.extract_countries

NCT_RE = re.compile(r"^NCT\d{8}$")
FIELDS = ["NCTId", "ContactsLocationsModule"]
CHANGE_REASON = "Backfilled countries from ClinicalTrials.gov API"


class Command(BaseCommand):
	help = (
		"Backfill Trials.countries / countries_by_source['ctgov'] from the "
		"ClinicalTrials.gov API for trials with an NCT identifier and no country data "
		"from any source."
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
			.filter(Q(countries__isnull=True) | Q(countries=""))
			.filter(Q(country_status__isnull=True) | Q(country_status=""))
			.filter(Q(countries_by_source__isnull=True) | Q(countries_by_source={}))
			.annotate(_trial_country_count=Count("trial_countries"))
			.filter(_trial_country_count=0)
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
			f"{total} NCT ids with no country data ({invalid} skipped as invalid)."
		)
		if not total:
			return

		api = ClinicalTrialsGovAPI()
		filled = no_locations = not_found = 0
		failed_batches = []
		unmapped_tokens = set()

		for start in range(0, total, batch_size):
			batch = nct_ids[start : start + batch_size]
			studies = self._fetch_batch(api, batch, sleep, failed_batches)
			if studies is None:
				continue

			countries_by_nct = {}
			for study in studies:
				ident = study.get("protocolSection", {}).get("identificationModule", {})
				nct = (ident.get("nctId") or "").strip().upper()
				if nct:
					countries_by_nct[nct] = _extract_countries(study)

			for nct in batch:
				if nct not in countries_by_nct:
					not_found += 1
					continue
				countries_str = countries_by_nct[nct]
				if not countries_str:
					no_locations += 1
					continue

				for token in _tokenize_countries_value(countries_str):
					code, region = _map_token(token)
					if not code and not region:
						unmapped_tokens.add(token)

				for trial in trials_by_nct[nct]:
					if not dry_run:
						self._apply_countries(trial, countries_str)
					filled += 1
					if verbosity >= 2:
						self.stdout.write(f"{nct}: {countries_str}")

			done = min(start + batch_size, total)
			self.stdout.write(
				f"Processed {done}/{total} NCT ids (filled {filled} trial rows)."
			)
			if sleep and done < total:
				time.sleep(sleep)

		prefix = "Would fill" if dry_run else "Filled"
		self.stdout.write(
			self.style.SUCCESS(
				f"{prefix} {filled} trial rows. No site locations on registry: "
				f"{no_locations} NCT ids. Not found on ClinicalTrials.gov: {not_found} NCT ids."
			)
		)
		if failed_batches:
			self.stdout.write(
				self.style.ERROR(
					f"{len(failed_batches)} batch(es) failed and were skipped — rerun to retry: "
					f"{', '.join(failed_batches[:5])}{'…' if len(failed_batches) > 5 else ''}"
				)
			)
		if unmapped_tokens:
			self.stdout.write(
				self.style.WARNING(
					f"{len(unmapped_tokens)} distinct country token(s) could not be mapped — "
					"review and extend the alias tables in "
					"gregory/utils/trial_field_normalizers.py:"
				)
			)
			for token in sorted(unmapped_tokens):
				self.stdout.write(f"  {token!r}")

	def _apply_countries(self, trial, countries_str):
		"""Write path for one trial (only called with a non-empty *countries_str*):
		merge into countries_by_source["ctgov"] (never a blind overwrite — preserves any
		other source's key, e.g. "ictrp") and set the legacy `countries` column, then
		save(). save() recomputes TrialCountry rows and regions_normalized from the raw
		country columns (Trials.save() -> sync_trial_countries()); bulk_update is
		deliberately never used here, see docs/trials-multi-source-merge.md.
		"""
		trial.countries_by_source = merge_countries_by_source(
			trial.countries_by_source, "ctgov", countries_str
		)
		trial.countries = countries_str
		trial._change_reason = CHANGE_REASON
		trial.save()

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
