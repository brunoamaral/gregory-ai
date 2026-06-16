"""
Management command to fetch clinical trials from ClinicalTrials.gov API.

This command processes Sources configured with method='ctgov_api' and fetches
clinical trials using the ClinicalTrials.gov REST API v2.

Source Configuration:
- method: 'ctgov_api'
- source_for: 'trials'
- ctgov_search_condition: The condition/disease search query (e.g., "multiple sclerosis")
- description: Optional additional search parameters:
    - "INTERVENTION:rituximab, ocrelizumab" - search by intervention/treatment
    - "TERM:some general search" - general search terms

Usage:
	python manage.py feedreader_trials_ctgov
	python manage.py feedreader_trials_ctgov --max-results 500
	python manage.py feedreader_trials_ctgov --verbosity=2
"""

from gregory.management.base import GregoryBaseCommand
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone
from gregory.classes import ClinicalTrialsGovAPI, ClinicalTrial
from gregory.models import Trials, Sources
from gregory.utils.registry_utils import (
	identifiers_conflict,
	merge_links,
	canonical_link,
	merge_identifiers,
	safe_change_reason,
)


class Command(GregoryBaseCommand):
	help = "Fetch clinical trials from ClinicalTrials.gov API sources"

	def add_arguments(self, parser):
		parser.add_argument(
			"--max-results",
			type=int,
			default=100,
			help="Maximum number of results to fetch per source (default: 100)",
		)
		parser.add_argument(
			"--source-id", type=int, help="Process only a specific source by ID"
		)
		parser.add_argument(
			"--debug",
			action="store_true",
			help="Show detailed data for each clinical trial found",
		)

	def handle(self, *args, **options):
		max_results = options.get("max_results", 100)
		source_id = options.get("source_id")
		self.debug = options.get("debug", False)

		self.api = ClinicalTrialsGovAPI()

		# Check API availability
		try:
			version_info = self.api.get_version()
			self.log(
				f"Connected to ClinicalTrials.gov API. Data timestamp: {version_info.get('dataTimestamp', 'unknown')}",
				level=1,
			)
		except Exception as e:
			self.log(
				f"Failed to connect to ClinicalTrials.gov API: {e}",
				level=1,
				style_func=self.style.ERROR,
			)
			return

		self.process_sources(max_results=max_results, source_id=source_id)

	def _print_trial_debug(self, clinical_trial):
		"""Print detailed debug information for a clinical trial."""
		self.stdout.write(self.style.WARNING("\n" + "=" * 80))
		self.stdout.write(
			self.style.WARNING(f"TRIAL: {clinical_trial.identifiers.get('nct', 'N/A')}")
		)
		self.stdout.write(self.style.WARNING("=" * 80))

		self.stdout.write(f"Title: {clinical_trial.title}")
		self.stdout.write(f"Link: {clinical_trial.link}")
		self.stdout.write(f"Published Date: {clinical_trial.published_date}")
		self.stdout.write(f"Identifiers: {clinical_trial.identifiers}")

		if clinical_trial.summary:
			summary_preview = (
				clinical_trial.summary[:500] + "..."
				if len(clinical_trial.summary) > 500
				else clinical_trial.summary
			)
			self.stdout.write(f"Summary: {summary_preview}")

		extras = clinical_trial.extra_fields or {}
		self.stdout.write("\nExtra Fields:")
		for key, value in extras.items():
			if value:
				# Truncate long values for display
				if isinstance(value, str) and len(value) > 200:
					value = value[:200] + "..."
				self.stdout.write(f"  {key}: {value}")

		self.stdout.write("")  # Empty line for separation

	def process_sources(self, max_results=100, source_id=None):
		"""Fetch and process trials from ClinicalTrials.gov API sources."""
		sources = Sources.objects.filter(
			method="ctgov_api", source_for="trials", active=True
		)

		if source_id:
			sources = sources.filter(source_id=source_id)

		if not sources.exists():
			self.log(
				"No active ClinicalTrials.gov API sources found.",
				level=1,
				style_func=self.style.WARNING,
			)
			return

		for source in sources:
			self.log(
				f"Processing ClinicalTrials.gov API source: {source.name}", level=1
			)
			created_count = 0
			updated_count = 0
			error_count = 0

			try:
				# Build search parameters from source configuration
				search_kwargs = self._build_search_params(source)

				# Fetch studies from the API
				for study_data in self.api.search_all(
					max_results=max_results, **search_kwargs
				):
					try:
						# Convert API response to ClinicalTrial object
						clinical_trial = self.api.parse_study_to_clinical_trial(
							study_data
						)

						if not clinical_trial.title:
							self.log(f"Skipping study with no title", level=3)
							continue

						# Debug output
						if self.debug:
							self._print_trial_debug(clinical_trial)

						# Check for existing trial
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
						self.stdout.write(
							self.style.ERROR(
								f"IntegrityError for trial '{clinical_trial.title[:50]}...' (NCT: {clinical_trial.identifiers.get('nct', 'N/A')}): {e}"
							)
						)
						error_count += 1
					except Exception as e:
						nct_id = (
							clinical_trial.identifiers.get("nct", "unknown")
							if clinical_trial
							else "unknown"
						)
						self.stdout.write(
							self.style.ERROR(f"Error processing trial {nct_id}: {e}")
						)
						error_count += 1

			except Exception as e:
				self.log(
					f"Error fetching from source {source.name}: {e}",
					level=1,
					style_func=self.style.ERROR,
				)

			self.log(
				f"Finished processing source: {source.name} - Created: {created_count}, Updated: {updated_count}, Errors: {error_count}",
				level=1,
				style_func=self.style.SUCCESS,
			)

	def _build_search_params(self, source):
		"""Build API search parameters from source configuration.

		Source configuration:
		- ctgov_search_condition: The condition/disease search query (e.g., "multiple sclerosis")
		- description: Optional additional search parameters:
		    - "INTERVENTION:rituximab, ocrelizumab" - search by intervention/treatment
		    - "TERM:some general search" - general search terms
		"""
		params = {
			"page_size": 100,
			"sort": ["LastUpdatePostDate:desc"],  # Get newest updates first
		}

		# Use ctgov_search_condition for the condition search query
		if source.ctgov_search_condition:
			params["query_cond"] = source.ctgov_search_condition

		# Use description field for additional search parameters (optional)
		# Format: "INTERVENTION:term1, term2" or just general terms
		if source.description:
			if source.description.upper().startswith("INTERVENTION:"):
				intervention_terms = source.description[
					13:
				].strip()  # Remove "INTERVENTION:" prefix
				params["query_intr"] = intervention_terms
			elif source.description.upper().startswith("TERM:"):
				general_terms = source.description[5:].strip()  # Remove "TERM:" prefix
				params["query_term"] = general_terms

		# Note: We don't specify fields to get the full study data
		# The API returns all fields by default

		return params

		return params

	def find_existing_trial(self, clinical_trial: ClinicalTrial):
		"""Find an existing trial by NCT ID, title, or link."""
		identifiers = clinical_trial.identifiers
		title = clinical_trial.title.lower() if clinical_trial.title else None

		# First try to find by NCT ID (most reliable)
		if identifiers.get("nct"):
			nct_id = identifiers["nct"]
			# Try exact match in identifiers JSON
			trial = Trials.objects.filter(identifiers__nct=nct_id).first()
			if trial:
				return trial
			# Also search for NCT ID in link field (e.g., https://clinicaltrials.gov/study/NCT12345)
			trial = Trials.objects.filter(link__icontains=nct_id).first()
			if trial:
				return trial

		# Try org study ID
		if identifiers.get("org_study_id"):
			# Check if org_study_id appears in identifiers JSON
			trial = Trials.objects.filter(
				Q(identifiers__org_study_id=identifiers["org_study_id"])
				| Q(secondary_id__icontains=identifiers["org_study_id"])
			).first()
			if trial:
				return trial

		# Try by link (ClinicalTrials.gov URL)
		if clinical_trial.link:
			trial = Trials.objects.filter(link=clinical_trial.link).first()
			if trial:
				return trial

		# Fallback to title match (case-insensitive) — only merge when the candidate
		# does not conflict on a shared registry key (Option B guard).
		if title:
			candidate = Trials.objects.filter(
				title__iexact=clinical_trial.title
			).first()
			if candidate and not identifiers_conflict(
				candidate.identifiers, clinical_trial.identifiers
			):
				self.log(f"Found trial by title: {candidate.title[:50]}...", level=3)
				return candidate

		return None

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
				# WHO-style fields
				scientific_title=extras.get("scientific_title"),
				acronym=extras.get("acronym"),
				primary_sponsor=extras.get("primary_sponsor"),
				recruitment_status=extras.get("recruitment_status"),
				date_registration=extras.get("date_registration"),
				study_type=extras.get("study_type"),
				phase=extras.get("phase"),
				countries=extras.get("countries"),
				inclusion_criteria=extras.get("inclusion_criteria"),
				exclusion_criteria=extras.get("exclusion_criteria"),
				intervention=extras.get("intervention"),
				secondary_id=extras.get("secondary_id"),
				condition=extras.get("condition"),
				primary_outcome=extras.get("primary_outcome"),
				secondary_outcome=extras.get("secondary_outcome"),
				inclusion_agemin=extras.get("inclusion_agemin"),
				inclusion_agemax=extras.get("inclusion_agemax"),
				inclusion_gender=extras.get("inclusion_gender"),
				target_size=extras.get("target_size"),
				contact_firstname=extras.get("contact_firstname"),
				contact_lastname=extras.get("contact_lastname"),
				contact_email=extras.get("contact_email"),
				contact_tel=extras.get("contact_tel"),
				source_register=extras.get("source_register"),
				ctg_detailed_description=extras.get("ctg_detailed_description"),
				results_posted=extras.get("results_posted", False),
				results_url_link=extras.get("results_url_link"),
				results_date_completed=extras.get("results_date_completed"),
				study_design=extras.get("study_design"),
				results_ipd_plan=extras.get("results_ipd_plan"),
				results_ipd_description=extras.get("results_ipd_description"),
				secondary_sponsor=extras.get("secondary_sponsor"),
				last_refreshed_on=extras.get("last_refreshed_on"),
				date_enrollement=extras.get("date_enrollement"),
				contact_affiliation=extras.get("contact_affiliation"),
			)

			if trial:
				trial.sources.add(source)
				trial._change_reason = safe_change_reason(
					f"Created from ClinicalTrials.gov API Source: {source.name}"
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

	def update_existing_trial(self, existing_trial, clinical_trial, source):
		"""Update an existing trial with new data only when necessary."""
		has_changes = False
		updated_fields = []

		# Update title only if it won't cause a duplicate conflict
		if existing_trial.title != clinical_trial.title and clinical_trial.title:
			# Check if the new title already exists (case-insensitive) for a different trial
			conflicting_trial = (
				Trials.objects.filter(title__iexact=clinical_trial.title)
				.exclude(pk=existing_trial.pk)
				.first()
			)
			if conflicting_trial:
				self.log(
					f"Skipping title update - would conflict with trial {conflicting_trial.trial_id}",
					level=2,
					style_func=self.style.WARNING,
				)
			else:
				existing_trial.title = clinical_trial.title
				has_changes = True
				updated_fields.append("title")

		if existing_trial.summary != clinical_trial.summary and clinical_trial.summary:
			existing_trial.summary = clinical_trial.summary
			has_changes = True
			updated_fields.append("summary")

		if (
			existing_trial.published_date != clinical_trial.published_date
			and clinical_trial.published_date
		):
			existing_trial.published_date = clinical_trial.published_date
			has_changes = True
			updated_fields.append("published_date")

		# Update identifiers (merge)
		merged_identifiers = merge_identifiers(
			existing_trial.identifiers, clinical_trial.identifiers
		)
		if merged_identifiers != existing_trial.identifiers:
			existing_trial.identifiers = merged_identifiers
			has_changes = True
			updated_fields.append("identifiers")

		# Record this source's URL under its registry key. The canonical link is
		# the first registry URL stored, chronologically — a later importer must
		# not replace it (see docs/trials-multi-source-merge.md). canonical_link
		# only changes it to upgrade an aggregator (WHO ICTRP) URL.
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

		# Update extra fields
		extras = getattr(clinical_trial, "extra_fields", {})
		extra_field_mapping = [
			"scientific_title",
			"primary_sponsor",
			"recruitment_status",
			"date_registration",
			"study_type",
			"phase",
			"countries",
			"inclusion_criteria",
			"exclusion_criteria",
			"intervention",
			"secondary_id",
			"condition",
			"primary_outcome",
			"secondary_outcome",
			"inclusion_agemin",
			"inclusion_agemax",
			"inclusion_gender",
			"target_size",
			"contact_firstname",
			"contact_lastname",
			"contact_email",
			"contact_tel",
			"source_register",
			"ctg_detailed_description",
			"results_posted",
			"results_url_link",
			"results_date_completed",
			"study_design",
			"results_ipd_plan",
			"results_ipd_description",
			"secondary_sponsor",
			"last_refreshed_on",
			"date_enrollement",
			"contact_affiliation",
		]

		for field in extra_field_mapping:
			new_value = extras.get(field)
			current_value = getattr(existing_trial, field, None)
			# Only update if new value is not None/empty and different from current
			if new_value and current_value != new_value:
				setattr(existing_trial, field, new_value)
				has_changes = True
				updated_fields.append(field)

		# Acronym is fill-once: a value set by an earlier import (e.g. WHO ICTRP)
		# is never replaced, mirroring the first-seen-wins rule for links.
		new_acronym = extras.get("acronym")
		if new_acronym and not existing_trial.acronym:
			existing_trial.acronym = new_acronym
			has_changes = True
			updated_fields.append("acronym")

		# Save if changes were detected
		if has_changes:
			existing_trial._change_reason = safe_change_reason(
				f"Updated from {source.name}: {', '.join(updated_fields[:3])}"
			)
			existing_trial.save()

		# Handle relationships
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
