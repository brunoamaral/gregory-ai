"""One-time backfill of Trials.acronym from the ClinicalTrials.gov API.

The CTgov ingester predates the acronym field, so trials sourced from
ClinicalTrials.gov have no acronym even when the registry publishes one.
This command fetches acronyms in bulk (filter.ids batches) for every trial
that has an NCT identifier and an empty acronym, and never overwrites an
existing value — WHO/ICTRP imports remain the authority for what they set.

Idempotent and resumable: rerunning only targets rows still missing an
acronym, so an interrupted run can simply be started again.
"""

import re
import time

from django.core.management.base import BaseCommand
from django.db.models import Q

from gregory.classes import ClinicalTrialsGovAPI
from gregory.models import Trials

NCT_RE = re.compile(r"^NCT\d{8}$")
ACRONYM_FIELDS = [
	"protocolSection.identificationModule.nctId",
	"protocolSection.identificationModule.acronym",
]
ACRONYM_MAX_LENGTH = Trials._meta.get_field("acronym").max_length
CHANGE_REASON = "Backfilled acronym from ClinicalTrials.gov API"


class Command(BaseCommand):
	help = "Backfill empty Trials.acronym values from the ClinicalTrials.gov API for trials with an NCT identifier."

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
			default=1.0,
			help="Seconds to wait between API requests (default: 1.0).",
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
			.filter(Q(acronym__isnull=True) | Q(acronym=""))
			.order_by("trial_id")
			.only("trial_id", "acronym", "identifiers")
		)
		if limit:
			candidates = candidates[:limit]

		# NCT ids carry a per-registry unique index, but normalise and map
		# defensively: pre-0054 rows may hold stray casing or whitespace.
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
			f"{total} NCT ids with no acronym ({invalid} skipped as invalid)."
		)
		if not total:
			return

		api = ClinicalTrialsGovAPI()
		updated = no_acronym = not_in_response = 0
		failed_batches = []

		for start in range(0, total, batch_size):
			batch = nct_ids[start : start + batch_size]
			studies = self._fetch_batch(api, batch, sleep, failed_batches)
			if studies is None:
				continue

			acronyms = {}
			for study in studies:
				ident = study.get("protocolSection", {}).get("identificationModule", {})
				nct = (ident.get("nctId") or "").strip().upper()
				if nct:
					acronyms[nct] = (ident.get("acronym") or "").strip()

			for nct in batch:
				if nct not in acronyms:
					not_in_response += 1
					continue
				acronym = acronyms[nct][:ACRONYM_MAX_LENGTH]
				if not acronym:
					no_acronym += 1
					continue
				for trial in trials_by_nct[nct]:
					if not dry_run:
						trial.acronym = acronym
						trial._change_reason = CHANGE_REASON
						trial.save(update_fields=["acronym"])
					updated += 1
					if verbosity >= 2:
						self.stdout.write(f"{nct}: {acronym}")

			done = min(start + batch_size, total)
			self.stdout.write(
				f"Processed {done}/{total} NCT ids (updated {updated} trial rows)."
			)
			if sleep and done < total:
				time.sleep(sleep)

		prefix = "Would update" if dry_run else "Updated"
		self.stdout.write(
			self.style.SUCCESS(
				f"{prefix} {updated} trial rows. "
				f"No acronym on registry: {no_acronym} NCT ids. Not returned by API: {not_in_response} NCT ids."
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
		"""Fetch one filter.ids batch, retrying once before skipping it."""
		for attempt in (1, 2):
			try:
				response = api.search(
					filter_ids=batch,
					fields=ACRONYM_FIELDS,
					page_size=len(batch),
					count_total=False,
				)
				return response.get("studies", [])
			except Exception as exc:
				if attempt == 1:
					self.stderr.write(
						self.style.WARNING(
							f"Batch {batch[0]}–{batch[-1]} failed ({exc}); retrying."
						)
					)
					time.sleep(sleep * 4)
				else:
					self.stderr.write(
						self.style.ERROR(
							f"Batch {batch[0]}–{batch[-1]} failed twice ({exc}); skipping."
						)
					)
					failed_batches.append(f"{batch[0]}–{batch[-1]}")
		return None
