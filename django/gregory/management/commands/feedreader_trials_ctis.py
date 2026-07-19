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

Usage:
	python manage.py feedreader_trials_ctis
	python manage.py feedreader_trials_ctis --limit 500
	python manage.py feedreader_trials_ctis --source-id 12 --sleep 1
"""

from datetime import timedelta

from gregory.management.base import GregoryBaseCommand
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone
from gregory.classes import ClinicalTrial, CTISPublicAPI
from gregory.models import Trials, Sources
from gregory.utils.registry_utils import (
	identifiers_conflict,
	merge_links,
	canonical_link,
	merge_identifiers,
	safe_change_reason,
)


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

	def handle(self, *args, **options):
		self.api = CTISPublicAPI()
		self.process_sources(
			sleep=options.get("sleep", 0.5),
			limit=options.get("limit"),
			source_id=options.get("source_id"),
		)

	def process_sources(self, sleep=0.5, limit=None, source_id=None):
		"""Fetch and process trials from CTIS public API sources.

		Per-source fetch failures are isolated and recorded in ``self.fetch_errors``
		so callers that need a hard failure signal (e.g. capture_trial_streams) can
		inspect them after the run.
		"""
		self.fetch_errors = []
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
						if existing_trial:
							self.update_existing_trial(
								existing_trial, clinical_trial, source
							)
							self.log(
								f"Updated existing trial: {existing_trial.title[:80]}...",
								level=2,
								style_func=self.style.SUCCESS,
							)
							updated_count += 1
						else:
							self.create_new_trial(clinical_trial, source)
							self.log(
								f"Created new trial: {clinical_trial.title[:80]}...",
								level=2,
								style_func=self.style.SUCCESS,
							)
							created_count += 1

					except IntegrityError as e:
						ct_number = (
							(clinical_trial.identifiers or {}).get("euct", "N/A")
							if clinical_trial
							else "N/A"
						)
						self.stdout.write(
							self.style.ERROR(
								f"IntegrityError for trial (CT number: {ct_number}): {e}"
							)
						)
						error_count += 1
					except Exception as e:
						ct_number = (
							(clinical_trial.identifiers or {}).get("euct", "unknown")
							if clinical_trial
							else "unknown"
						)
						self.stdout.write(
							self.style.ERROR(f"Error processing trial {ct_number}: {e}")
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
		# fill it when the trial doesn't have one yet (see
		# CTIS-API-FEEDREADER-PLAN.md section 3, "summary" row).
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
