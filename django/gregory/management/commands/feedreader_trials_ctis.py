"""
Management command to fetch clinical trials from the CTIS public search API
(euclinicaltrials.eu/ctis-public-api).

This command processes Sources configured with method='ctis_api' and fetches the
FULL result set for each source's search criteria — unlike the RSS feed
(method='rss', still active as the EMA-advertised fallback channel; see
feedreader_trials.py), which only returns the 15 most recently updated trials per
query and therefore never enriches CTIS-identified trials that entered the DB via
WHO ICTRP XML or ClinicalTrials.gov cross-identifiers. The RSS source is
intentionally never retired: the CTIS API is undocumented (reverse-engineered from
the public search portal's network calls — see docs/ctis-public-api-schema.md) and
access could be withdrawn without notice.

Source Configuration:
- method: 'ctis_api'
- source_for: 'trials'
- ctis_search_criteria: verbatim searchCriteria dict POSTed to the API, e.g.
    {"medicalCondition": "Multiple Sclerosis"}

For every record /search returns, this command also fetches the full /retrieve
dossier for that trial once, archives it to disk (gregory.utils.ctis_backup) —
one deduplicated JSON snapshot per trial per distinct content version — and
enriches the trial from it (see docs/ctis-public-api-schema.md for the payload
shape, and CTIS-API-PHASE-2-PLAN.md for the enrichment design):

1. All participating countries, including non-EEA (`rowCountriesInfo`), filed
   under the "ctis" key of `countries_by_source`.
2. Per-country recruitment start dates (`authorizedPartsII[].mscInfo`), stored
   in `countries_recruitment_date` and, via `Trials.sync_trial_countries()`, on
   `TrialCountry.recruitment_start_date`.
3. Structured eligibility criteria, filling `inclusion_criteria`/
   `exclusion_criteria` only when they are still empty (WHO ICTRP legitimately
   populates those columns for the same trials and must never be overwritten).

Enrichment failures (404, unexpected shape) are logged and skipped per trial —
they never abort the source run. `--enrich-all` re-runs enrichment for every
trial holding a `euct`/`ctis` identifier, independent of the search sync; this
is both the one-time backfill sweep and the general re-run path, and is
idempotent by construction (every write above is a deterministic replacement
or a fill-if-empty).

Usage:
	python manage.py feedreader_trials_ctis
	python manage.py feedreader_trials_ctis --limit 500
	python manage.py feedreader_trials_ctis --source-id 12 --sleep 1
	python manage.py feedreader_trials_ctis --enrich-all --sleep 0.5
"""

import logging
import re
import time
from datetime import timedelta

from gregory.management.base import GregoryBaseCommand
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone
from gregory.classes import ClinicalTrial, CTISPublicAPI
from gregory.models import Trials, Sources
from gregory.utils.ctis_backup import save_retrieve_backup
from gregory.utils.registry_utils import (
	identifiers_conflict,
	merge_links,
	canonical_link,
	merge_identifiers,
	merge_countries_by_source,
	safe_change_reason,
)
from gregory.utils.trial_field_normalizers import _name_to_code_lookup

logger = logging.getLogger(__name__)

# Raw /retrieve JSON dossiers are archived here (one file per distinct content
# snapshot per trial; see gregory.utils.ctis_backup). Matches the persisted-volume
# convention of capture_trial_streams.py's DEFAULT_DIR.
BACKUPS_DIR = "/code/backups"

# A genuine CTIS number is 4 hyphen-separated segments (YYYY-NNNNNN-NN-NN, e.g.
# "2025-523726-40-00") — distinct from the 3-segment legacy EudraCT/EUCTR format
# (YYYY-NNNNNN-NN) that some pre-CTIS trials also carry under the "euct" identifier
# key (a historic convention from manual/legacy imports, predating this command).
# --enrich-all's DB query can't cheaply distinguish the two (or a present-but-null
# "euct" key, which every ClinicalTrials.gov/WHO-imported trial has as a template
# placeholder) at the SQL level, so this regex gates the actual /retrieve GET —
# without it, a non-CTIS ct_number 400s against the CTIS API.
CTIS_NUMBER_RE = re.compile(r"^\d{4}-\d{6}-\d{2}-\d{2}$")


def _extract_row_countries(payload: dict) -> list:
	"""authorizedApplication.authorizedPartI.rowCountriesInfo[] -> the list of
	{"name", "isoAlpha2Code", "isoAlpha3Code", ...} dicts, or [] if the subtree is
	missing/malformed (the API is undocumented — parse defensively, never raise)."""
	part_i = (payload.get("authorizedApplication") or {}).get("authorizedPartI") or {}
	countries = part_i.get("rowCountriesInfo")
	if not isinstance(countries, list):
		return []
	return [c for c in countries if isinstance(c, dict) and c.get("name")]


def _extract_eligibility_criteria(payload: dict) -> tuple:
	"""authorizedPartI.trialDetails.trialInformation.eligibilityCriteria ->
	(inclusion_text, exclusion_text): each a newline-separated block, one line per
	criterion as "<number>. <text>", sorted by number; None if the subtree is
	missing/malformed or has no usable entries."""
	part_i = (payload.get("authorizedApplication") or {}).get("authorizedPartI") or {}
	criteria = (
		(part_i.get("trialDetails") or {}).get("trialInformation") or {}
	).get("eligibilityCriteria")
	if not isinstance(criteria, dict):
		return None, None

	def _join(entries, text_key):
		if not isinstance(entries, list):
			return None
		lines = []
		for entry in entries:
			if not isinstance(entry, dict):
				continue
			text = entry.get(text_key)
			if not text:
				continue
			number = entry.get("number")
			lines.append((number if isinstance(number, int) else 10**9, number, text))
		if not lines:
			return None
		lines.sort(key=lambda item: item[0])
		return "\n".join(
			f"{number}. {text}" if number is not None else text
			for _, number, text in lines
		)

	inclusion = _join(criteria.get("principalInclusionCriteria"), "principalInclusionCriteria")
	exclusion = _join(criteria.get("principalExclusionCriteria"), "principalExclusionCriteria")
	return inclusion, exclusion


def _extract_recruitment_dates(payload: dict) -> dict:
	"""authorizedApplication.authorizedPartsII[].mscInfo -> {alpha2_code: earliest
	ISO recruitmentStartDate}. Country name -> code via the same cached
	display-name lookup trial_field_normalizers uses for countries_by_source, so
	the two never disagree on a mapping. Unmappable names or a part with no
	recruitment period are skipped (logged), never raising."""
	parts_ii = (payload.get("authorizedApplication") or {}).get("authorizedPartsII")
	if not isinstance(parts_ii, list):
		return {}

	name_to_code = _name_to_code_lookup()
	dates = {}
	for part in parts_ii:
		if not isinstance(part, dict):
			continue
		msc_info = part.get("mscInfo")
		if not isinstance(msc_info, dict):
			continue
		country_name = msc_info.get("countryName")
		periods = msc_info.get("trialRecruitmentPeriod")
		if not country_name or not isinstance(periods, list):
			continue
		code = name_to_code.get(str(country_name).strip().casefold())
		if not code:
			logger.info("CTIS retrieve: unmapped recruitment-date country %r", country_name)
			continue
		candidates = [
			p.get("recruitmentStartDate")
			for p in periods
			if isinstance(p, dict) and p.get("recruitmentStartDate")
		]
		if not candidates:
			continue
		earliest = min(candidates)
		if code not in dates or earliest < dates[code]:
			dates[code] = earliest
	return dates


class Command(GregoryBaseCommand):
	help = "Fetch clinical trials from the CTIS public search API sources"

	def add_arguments(self, parser):
		parser.add_argument(
			"--sleep",
			type=float,
			default=0.5,
			help="Seconds to sleep between page requests (default: 0.5)",
		)
		parser.add_argument(
			"--limit",
			type=int,
			default=None,
			help="Maximum number of records to process per source (for smoke tests)",
		)
		parser.add_argument(
			"--source-id", type=int, help="Process only a specific source by ID"
		)
		parser.add_argument(
			"--backup-dir",
			default=None,
			help="Directory to archive raw /retrieve JSON dossiers in (default: %s)"
			% BACKUPS_DIR,
		)
		parser.add_argument(
			"--enrich-all",
			action="store_true",
			help="Skip the search sync entirely and instead re-run the /retrieve "
			"enrichment (countries, recruitment dates, eligibility criteria) for "
			"every trial holding a euct/ctis identifier. This is both the one-time "
			"backfill sweep and the general re-run path; idempotent.",
		)

	def handle(self, *args, **options):
		self.api = CTISPublicAPI()
		if options.get("enrich_all"):
			self.enrich_all_trials(
				sleep=options.get("sleep", 0.5), limit=options.get("limit")
			)
			return
		self.process_sources(
			sleep=options.get("sleep", 0.5),
			limit=options.get("limit"),
			source_id=options.get("source_id"),
			backup_dir=options.get("backup_dir"),
		)

	def enrich_all_trials(self, sleep=0.5, limit=None):
		"""One-time sweep / general re-run path: re-fetch /retrieve and re-apply the
		enrichment for every trial already holding a genuine CTIS-format identifier,
		regardless of whether the search sync touched it this run.

		The `has_key` query below is a coarse net, not the real filter: it also
		matches trials where "euct" is present but null (every ClinicalTrials.gov/
		WHO-imported trial carries that key as an unset template field) and trials
		whose "euct" holds a legacy 3-segment EudraCT/EUCTR number rather than a
		CTIS number — a manual/legacy-import convention that predates this command.
		CTIS_NUMBER_RE is what actually decides whether a GET is worth firing;
		everything else is skipped (logged) before it can 400 against the API.
		"""
		trials = Trials.objects.filter(
			Q(identifiers__has_key="euct") | Q(identifiers__has_key="ctis")
		)
		processed = 0
		enriched = 0
		skipped = 0
		for trial in trials.iterator():
			identifiers = trial.identifiers or {}
			ct_number = identifiers.get("euct")
			if not ct_number:
				ctis_value = identifiers.get("ctis") or ""
				ct_number = ctis_value[len("CTIS"):] if ctis_value.startswith("CTIS") else ctis_value
			if not ct_number or not CTIS_NUMBER_RE.match(ct_number):
				skipped += 1
				self.log(
					f"Skipping trial {trial.pk}: {ct_number!r} is not a CTIS-format "
					"ct number",
					level=3,
				)
				continue

			processed += 1
			payload = self._fetch_retrieve_payload(ct_number)
			if payload is not None:
				self._enrich_from_retrieve(trial, payload)
				enriched += 1

			if sleep:
				time.sleep(sleep)
			if limit and processed >= limit:
				break

		self.log(
			f"CTIS enrich-all: processed {processed} trial(s), enriched {enriched}, "
			f"skipped {skipped} non-CTIS identifier(s).",
			level=1,
			style_func=self.style.SUCCESS,
		)

	def process_sources(self, sleep=0.5, limit=None, source_id=None, backup_dir=None):
		"""Fetch and process trials from CTIS public API sources.

		Per-source fetch failures are isolated and recorded in ``self.fetch_errors``
		so callers that need a hard failure signal (e.g. capture_trial_streams) can
		inspect them after the run.
		"""
		self.fetch_errors = []
		backup_dir = backup_dir or BACKUPS_DIR
		sources = Sources.objects.filter(
			method="ctis_api", source_for="trials", active=True
		)

		if source_id:
			sources = sources.filter(source_id=source_id)

		if not sources.exists():
			self.log(
				"No active CTIS public API sources found.",
				level=1,
				style_func=self.style.WARNING,
			)
			return

		for source in sources:
			self.log(f"Processing CTIS public API source: {source.name}", level=1)

			criteria = source.ctis_search_criteria
			if not isinstance(criteria, dict) or not criteria:
				self.log(
					f"  Skipping source '{source.name}': ctis_search_criteria is "
					f"{'empty' if criteria == {} else 'missing or not a dict'}. An "
					"empty dict would fetch the entire CTIS registry, so this source "
					"is never fetched until a valid searchCriteria is configured.",
					level=1,
					style_func=self.style.ERROR,
				)
				continue

			created_count = 0
			updated_count = 0
			error_count = 0
			fetched_count = 0
			fetch_failed = False
			# Anchor candidate: taken BEFORE paging starts, so trials updated while
			# we page are re-covered by the next run's window.
			fetch_started = timezone.now()

			since = None
			if source.last_successful_fetch_at:
				since = (source.last_successful_fetch_at - timedelta(days=2)).date()
				self.log(
					f"  Incremental window: records updated since {since} "
					f"(anchor {source.last_successful_fetch_at:%Y-%m-%d %H:%M} UTC minus "
					"2-day overlap)",
					level=1,
				)
			else:
				self.log(
					"  No previous successful fetch recorded; fetching the full result "
					"set (backfill mode). For large conditions run a manual backfill "
					"with --source-id and a high --limit.",
					level=1,
					style_func=self.style.WARNING,
				)

			try:
				for record in self.api.iter_search(criteria, since=since, sleep=sleep):
					fetched_count += 1
					clinical_trial = None
					try:
						clinical_trial = self.api.parse_ctis_search_record(record)
						if not clinical_trial.title or not (
							clinical_trial.identifiers or {}
						).get("euct"):
							self.log(
								"Skipping record with no title or ctNumber", level=3
							)
							continue

						existing_trial = self.find_existing_trial(clinical_trial)

						# Skip the expensive /retrieve GET for records outside the
						# incremental window — iter_search still yields them (so the
						# cheap DB non-destructive-update below still runs), but a
						# wholly-stale trailing page can otherwise cost up to `size`
						# unnecessary heavy GETs per run for trials that haven't changed.
						# Fetched once and reused for both the disk archive and the DB
						# enrichment below, so a changed trial costs exactly one GET.
						# A genuinely new trial always gets fetched regardless of
						# staleness: record_is_stale is about a record being
						# *unchanged*, not about it being new to our DB — a trailing
						# page can still surface a trial we've never stored (e.g. an
						# aged registry entry seen for the first time), and skipping
						# it here would leave that trial with no archive and no
						# enrichment at all.
						retrieve_payload = None
						if (
							since is None
							or existing_trial is None
							or not self.api.record_is_stale(record, since)
						):
							retrieve_payload = self._fetch_retrieve_payload(
								clinical_trial.identifiers["euct"]
							)
							if retrieve_payload is not None:
								self._archive_retrieve_payload(
									retrieve_payload,
									clinical_trial.identifiers["euct"],
									backup_dir,
								)

						if existing_trial:
							self.update_existing_trial(
								existing_trial, clinical_trial, source
							)
							trial_obj = existing_trial
							self.log(
								f"Updated existing trial: {existing_trial.title[:80]}...",
								level=2,
								style_func=self.style.SUCCESS,
							)
							updated_count += 1
						else:
							trial_obj = self.create_new_trial(clinical_trial, source)
							self.log(
								f"Created new trial: {clinical_trial.title[:80]}...",
								level=2,
								style_func=self.style.SUCCESS,
							)
							created_count += 1

						if retrieve_payload is not None and trial_obj is not None:
							self._enrich_from_retrieve(trial_obj, retrieve_payload)

					except IntegrityError as e:
						ct_number = (
							(clinical_trial.identifiers or {}).get("euct", "N/A")
							if clinical_trial
							else "N/A"
						)
						self.log(
							f"IntegrityError for trial (CT number: {ct_number}): {e}",
							level=1,
							style_func=self.style.ERROR,
						)
						error_count += 1
					except Exception as e:
						ct_number = (
							(clinical_trial.identifiers or {}).get("euct", "unknown")
							if clinical_trial
							else "unknown"
						)
						self.log(
							f"Error processing trial {ct_number}: {e}",
							level=1,
							style_func=self.style.ERROR,
						)
						error_count += 1

					if limit and fetched_count >= limit:
						break

			except Exception as e:
				fetch_failed = True
				self.fetch_errors.append(f"{source.name}: {e}")
				self.log(
					f"Error fetching from source {source.name}: {e}",
					level=1,
					style_func=self.style.ERROR,
				)

			# Advance the incremental anchor only after a fully successful run: every
			# page consumed, no request failure, no item errors, cap not hit. Anything
			# less means this window may hold trials we did not store, so the next run
			# must re-cover it.
			if fetch_failed:
				self.log(
					f"  Not advancing incremental anchor for {source.name}: fetch did not complete.",
					level=1,
					style_func=self.style.WARNING,
				)
			elif limit and fetched_count >= limit:
				self.log(
					f"  Result cap of {limit} hit for {source.name}; not advancing the "
					"incremental anchor so the next run re-covers this window.",
					level=1,
					style_func=self.style.WARNING,
				)
			elif error_count:
				self.log(
					f"  {error_count} trial(s) failed to process for {source.name}; not "
					"advancing the incremental anchor.",
					level=1,
					style_func=self.style.WARNING,
				)
			else:
				source.last_successful_fetch_at = fetch_started
				source.save(update_fields=["last_successful_fetch_at"])

			self.log(
				f"Finished processing source: {source.name} - Created: {created_count}, "
				f"Updated: {updated_count}, Errors: {error_count}",
				level=1,
				style_func=self.style.SUCCESS,
			)

	def _fetch_retrieve_payload(self, ct_number):
		"""Fetch the full /retrieve dossier for ct_number once. Returns None (logged)
		on a 404, a transport failure, or an unexpected response shape — the API is
		undocumented, so any failure here must never block or fail the actual
		create/update of a trial. Shared by the disk archive and the DB enrichment
		so a changed trial costs exactly one GET regardless of how many downstream
		uses consume the payload.
		"""
		try:
			payload = self.api.retrieve(ct_number)
		except Exception as e:
			self.log(
				f"Failed to retrieve dossier for {ct_number}: {e}",
				level=2,
				style_func=self.style.WARNING,
			)
			return None
		if payload is None:
			self.log(
				f"No retrieve dossier for {ct_number} (404 or not retrievable); skipping",
				level=3,
			)
			return None
		if not isinstance(payload, dict):
			self.log(
				f"Skipping retrieve for {ct_number}: did not return a JSON object",
				level=3,
				style_func=self.style.WARNING,
			)
			return None
		return payload

	def _archive_retrieve_payload(self, payload, ct_number, backup_dir):
		"""Archive an already-fetched /retrieve payload (deduplicated by content —
		see gregory.utils.ctis_backup.save_retrieve_backup). A side archive, not
		required for correctness of the trial data this command stores (that comes
		entirely from /search) — any failure here (disk full, ...) is logged and
		swallowed so it can never block or fail the actual create/update of a trial.
		"""
		try:
			save_retrieve_backup(backup_dir, ct_number, payload)
		except Exception as e:
			self.log(
				f"Failed to save retrieve backup for {ct_number}: {e}",
				level=2,
				style_func=self.style.WARNING,
			)

	def _enrich_from_retrieve(self, trial, payload):
		"""Harvest all-countries, per-country recruitment start dates, and
		eligibility criteria from an already-fetched /retrieve payload onto `trial`,
		then persist with a single save() (which also recomputes TrialCountry rows
		and regions_normalized — see Trials.save()/sync_trial_countries()). No save
		is issued when none of the three items yielded anything (e.g. a malformed
		or unexpectedly bare payload) — nothing to persist, so no pointless write
		or history row.

		Never raises: a parse failure in one item logs and is skipped without
		blocking the others, and any failure here never fails the source run.
		"""
		try:
			changed = (
				self._enrich_countries_by_source(trial, payload)
				| self._enrich_recruitment_dates(trial, payload)
				| self._enrich_eligibility_criteria(trial, payload)
			)
		except Exception as e:
			self.log(
				f"CTIS retrieve enrichment failed for trial {trial.pk}: {e}",
				level=1,
				style_func=self.style.ERROR,
			)
			return

		if not changed:
			return

		trial._change_reason = safe_change_reason(
			"Enriched from CTIS retrieve endpoint"
		)
		trial.save()

	def _enrich_countries_by_source(self, trial, payload) -> bool:
		"""Item 1: all participating countries (incl. non-EEA) -> countries_by_source
		["ctis"], semicolon-joined display names (the tokenizer's guaranteed mapping
		path — see CTIS-API-PHASE-2-PLAN.md). Union with other sources' keys via
		merge_countries_by_source; never touches another source's key. Returns
		whether the merged map actually changed — re-enriching with an unchanged
		payload must not trigger a save()/history row."""
		row_countries = _extract_row_countries(payload)
		value = "; ".join(c["name"] for c in row_countries if c.get("name"))
		if not value:
			return False
		merged = merge_countries_by_source(trial.countries_by_source, "ctis", value)
		if merged == trial.countries_by_source:
			return False
		trial.countries_by_source = merged
		return True

	def _enrich_recruitment_dates(self, trial, payload) -> bool:
		"""Item 2: per-country recruitment start dates -> countries_recruitment_date
		(CTIS-only raw column, mirrors countries_decision_date). Replaced wholesale
		with the freshly computed dict on every enrichment — deterministic and
		idempotent, like every other CTIS-only field. Left untouched when nothing
		parses (defensive: never wipe a previously-recorded value on a degraded
		response). Returns whether the dict actually changed — re-enriching with an
		unchanged payload must not trigger a save()/history row."""
		dates = _extract_recruitment_dates(payload)
		if not dates or dates == trial.countries_recruitment_date:
			return False
		trial.countries_recruitment_date = dates
		return True

	def _enrich_eligibility_criteria(self, trial, payload) -> bool:
		"""Item 3: structured eligibility criteria fill inclusion_criteria/
		exclusion_criteria only when still empty — WHO ICTRP legitimately populates
		those columns for the same trials and must never be overwritten. Returns
		whether a value was written."""
		inclusion, exclusion = _extract_eligibility_criteria(payload)
		changed = False
		if inclusion and not trial.inclusion_criteria:
			trial.inclusion_criteria = inclusion
			changed = True
		if exclusion and not trial.exclusion_criteria:
			trial.exclusion_criteria = exclusion
			changed = True
		return changed

	def find_existing_trial(self, clinical_trial: ClinicalTrial):
		"""Find an existing trial for this CTIS number, bridging the two identifier
		conventions in use across importers:

		- RSS / this command write the bare ctNumber under the "euct" key (see
		  EUTrialParser.extract_identifiers / CTISPublicAPI.parse_ctis_search_record).
		- WHO ICTRP XML import (importWHOXML.py) derives its dict key from the
		  registry-prefixed TrialID it exports, which for CTIS trials is "ctis" with
		  a "CTIS"-prefixed value (e.g. "CTIS2025-523726-40-00").

		Without checking both, this command would create a duplicate trial for every
		WHO-originated CTIS record instead of enriching it — the exact gap this
		feedreader exists to close.
		"""
		identifiers = clinical_trial.identifiers or {}
		euct = identifiers.get("euct")
		title = clinical_trial.title.lower() if clinical_trial.title else None

		query = Q()
		if euct:
			query |= Q(identifiers__euct=euct)
			query |= Q(identifiers__ctis=f"CTIS{euct}")

		if query:
			trial = Trials.objects.filter(query).first()
			if trial:
				return trial

		if clinical_trial.link:
			trial = Trials.objects.filter(link=clinical_trial.link).first()
			if trial:
				return trial

		# Fallback to title match (case-insensitive) — only merge when the candidate
		# does not conflict on a shared registry key (Option B guard).
		if title:
			candidate = Trials.objects.filter(title__iexact=clinical_trial.title).first()
			if candidate and not identifiers_conflict(
				candidate.identifiers, clinical_trial.identifiers
			):
				self.log(f"Found trial by title: {candidate.title[:80]}...", level=3)
				return candidate

		return None

	# Extra fields parse_ctis_search_record produces — identical set to what
	# EUTrialParser.parse_summary returns, so the same non-destructive update guard
	# used by the RSS command applies unchanged.
	EXTRA_FIELD_MAPPING = [
		"source_register",
		"therapeutic_areas",
		"country_status",
		"trial_region",
		"results_posted",
		"overall_decision_date",
		"countries_decision_date",
		"sponsor_type",
		"condition",
		"recruitment_status",
		"primary_outcome",
		"secondary_outcome",
		"primary_sponsor",
		"phase",
		"intervention",
		"inclusion_agemin",
		"inclusion_agemax",
		"inclusion_gender",
		"target_size",
		"last_refreshed_on",
	]

	def create_new_trial(self, clinical_trial: ClinicalTrial, source):
		"""Create a new trial in the database."""
		extras = getattr(clinical_trial, "extra_fields", {})
		try:
			trial = Trials.objects.create(
				discovery_date=timezone.now(),
				title=clinical_trial.title,
				summary=clinical_trial.summary,
				link=clinical_trial.link,
				links=merge_links(None, clinical_trial.link),
				published_date=clinical_trial.published_date,
				identifiers=clinical_trial.identifiers,
				source_register=extras.get("source_register"),
				therapeutic_areas=extras.get("therapeutic_areas"),
				country_status=extras.get("country_status"),
				trial_region=extras.get("trial_region"),
				results_posted=extras.get("results_posted") or False,
				overall_decision_date=extras.get("overall_decision_date"),
				countries_decision_date=extras.get("countries_decision_date"),
				sponsor_type=extras.get("sponsor_type"),
				condition=extras.get("condition"),
				recruitment_status=extras.get("recruitment_status"),
				primary_outcome=extras.get("primary_outcome"),
				secondary_outcome=extras.get("secondary_outcome"),
				primary_sponsor=extras.get("primary_sponsor"),
				phase=extras.get("phase"),
				intervention=extras.get("intervention"),
				inclusion_agemin=extras.get("inclusion_agemin"),
				inclusion_agemax=extras.get("inclusion_agemax"),
				inclusion_gender=extras.get("inclusion_gender"),
				target_size=extras.get("target_size"),
				last_refreshed_on=extras.get("last_refreshed_on"),
			)
			if trial:
				trial.sources.add(source)
				trial._change_reason = safe_change_reason(
					f"Created from CTIS public API Source: {source.name}"
				)
				trial.save()
				if source.team:
					trial.teams.add(source.team)
				else:
					self.log(
						f"Warning: Source '{source.name}' has no team assigned. Skipping team association.",
						level=1,
						style_func=self.style.WARNING,
					)
				if source.subject:
					trial.subjects.add(source.subject)
				trial._change_reason = safe_change_reason(
					f"Added relationships Team: {source.team} Subject: {source.subject}"
				)
				trial.save()
			return trial
		except IntegrityError as e:
			self.log(
				f"Integrity error during trial creation: {e}",
				level=3,
				style_func=self.style.ERROR,
			)
			raise

	def update_existing_trial(self, existing_trial, clinical_trial: ClinicalTrial, source):
		"""Update an existing trial with new data only when necessary."""
		has_changes = False
		updated_fields = []

		if clinical_trial.title and existing_trial.title != clinical_trial.title:
			existing_trial.title = clinical_trial.title
			has_changes = True
			updated_fields.append("title")

		# summary is fill-once: EUTrialParser/CTISPublicAPI both compose a
		# deterministic labeled-line summary from mapped fields, which would churn
		# on every sync if compared for equality like title/published_date. Only
		# fill it when the trial doesn't have one yet.
		if clinical_trial.summary and not existing_trial.summary:
			existing_trial.summary = clinical_trial.summary
			has_changes = True
			updated_fields.append("summary")

		if (
			clinical_trial.published_date
			and existing_trial.published_date != clinical_trial.published_date
		):
			existing_trial.published_date = clinical_trial.published_date
			has_changes = True
			updated_fields.append("published_date")

		merged_identifiers = merge_identifiers(
			existing_trial.identifiers, clinical_trial.identifiers
		)
		if merged_identifiers != existing_trial.identifiers:
			existing_trial.identifiers = merged_identifiers
			has_changes = True
			updated_fields.append("identifiers")

		# Record this source's URL under its registry key. The canonical link is the
		# first registry URL stored, chronologically — a later importer must not
		# replace it (see docs/trials-multi-source-merge.md). canonical_link only
		# changes it to upgrade an aggregator (WHO ICTRP) URL.
		merged_links = merge_links(existing_trial.links, clinical_trial.link)
		if merged_links != (existing_trial.links or {}):
			existing_trial.links = merged_links
			has_changes = True
			updated_fields.append("links")
		new_link = canonical_link(existing_trial.links, existing_trial.link)
		if new_link and existing_trial.link != new_link:
			existing_trial.link = new_link
			has_changes = True
			updated_fields.append("link")

		extras = getattr(clinical_trial, "extra_fields", {})
		for field in self.EXTRA_FIELD_MAPPING:
			if (
				field in extras
				and extras[field] not in (None, "")
				and getattr(existing_trial, field) != extras[field]
			):
				setattr(existing_trial, field, extras[field])
				has_changes = True
				updated_fields.append(field)

		if has_changes:
			existing_trial._change_reason = safe_change_reason(
				f"Updated from {source.name}: {', '.join(updated_fields[:3])}"
			)
			existing_trial.save()

		if source.subject and source.subject not in existing_trial.subjects.all():
			existing_trial.subjects.add(source.subject)
			existing_trial._change_reason = safe_change_reason(
				f"Added subject: {source.subject}"
			)
			existing_trial.save()

		if source not in existing_trial.sources.all():
			existing_trial.sources.add(source)
			existing_trial._change_reason = safe_change_reason(
				f"Added source: {source.name}"
			)
			existing_trial.save()

		if source.team and source.team not in existing_trial.teams.all():
			existing_trial.teams.add(source.team)
			existing_trial._change_reason = safe_change_reason(
				f"Added team: {source.team}"
			)
			existing_trial.save()
