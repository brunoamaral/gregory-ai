from dateutil.parser import parse
from gregory.management.base import GregoryBaseCommand
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone
from gregory.classes import ClinicalTrial, EUTrialParser
from gregory.functions import remove_utm
from gregory.models import Trials, Sources
from gregory.utils.registry_utils import (
	identifiers_conflict,
	merge_links,
	canonical_link,
	merge_identifiers,
	safe_change_reason,
)
import feedparser
import pytz
import requests


class Command(GregoryBaseCommand):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.eu_parser = EUTrialParser()

	def handle(self, *args, **options):
		self.setup()
		self.process_feeds()

	def setup(self):
		self.tzinfos = {
			"EDT": pytz.timezone("America/New_York"),
			"EST": pytz.timezone("America/New_York"),
		}

	def process_feeds(self):
		"""Fetch and process RSS feeds for clinical trials."""
		sources = Sources.objects.filter(method="rss", source_for="trials", active=True)
		for source in sources:
			self.log(f"Processing RSS feed: {source.name}", level=1)
			# One broken source (timeout, DNS, SSL) must not abort the whole run
			# and silently skip every source after it in the loop.
			try:
				if not source.ignore_ssl:
					feed = feedparser.parse(source.link)
				else:
					response = requests.get(source.link, verify=False, timeout=30)
					feed = feedparser.parse(response.content)
			except Exception as e:
				self.log(
					f"Failed to fetch feed for source '{source.name}' ({source.link}): {e}. "
					"Skipping this source.",
					level=1,
					style_func=self.style.ERROR,
				)
				continue
			for entry in feed["entries"]:
				try:
					# Extract trial details
					summary_html = entry.get("summary_detail", {}).get(
						"value", ""
					) or entry.get("summary", "")
					published = self.parse_date(entry.get("published"))
					link = remove_utm(entry["link"])
					identifiers = self.eu_parser.extract_identifiers(
						link, entry.get("guid")
					)
					extra_fields = {}
					if "euclinicaltrials.eu" in link:
						extra_fields = self.eu_parser.parse_summary(summary_html)

					# Create ClinicalTrial object
					incoming_clinical_trial = ClinicalTrial(
						title=entry["title"],
						summary=summary_html,
						link=link,
						published_date=published,
						identifiers=identifiers,
						extra_fields=extra_fields,
					)

					# Check for existing trial
					existing_trial = self.find_existing_trial(incoming_clinical_trial)
					if existing_trial:
						self.update_existing_trial(
							existing_trial, incoming_clinical_trial, source
						)
						self.log(
							f"Updated existing trial: {existing_trial.title}",
							level=2,
							style_func=self.style.SUCCESS,
						)
						continue

					# Create new trial if no existing trial is found
					self.create_new_trial(incoming_clinical_trial, source)
					self.log(
						f"Created new trial: {incoming_clinical_trial.title}",
						level=2,
						style_func=self.style.SUCCESS,
					)

				except IntegrityError as e:
					self.log(
						f"IntegrityError for trial '{entry.get('title')}' at link "
						f"{entry.get('link', '<no link>')}: {e}",
						level=3,
						style_func=self.style.ERROR,
					)
				except Exception as e:
					self.log(
						f"Error processing trial '{entry.get('title')}' at link "
						f"{entry.get('link', '<no link>')}: {e}",
						level=3,
						style_func=self.style.ERROR,
					)

			self.log(
				f"Finished processing RSS feed: {source.name}",
				level=1,
				style_func=self.style.SUCCESS,
			)

	def parse_date(self, date_str: str):
		"""Parse a date string into a timezone-aware datetime."""
		if not date_str:
			return None
		return parse(date_str, tzinfos=self.tzinfos).astimezone(pytz.utc)

	def find_existing_trial(self, clinical_trial: ClinicalTrial):
		identifiers = clinical_trial.identifiers
		title = clinical_trial.title.lower() if clinical_trial.title else None

		query = Q()
		if identifiers.get("euct"):
			query |= Q(identifiers__euct=identifiers["euct"])
		if identifiers.get("nct"):
			query |= Q(identifiers__nct=identifiers["nct"])
		if identifiers.get("eudract"):
			query |= Q(identifiers__eudract=identifiers["eudract"])
		if identifiers.get("ctis"):
			query |= Q(identifiers__ctis=identifiers["ctis"])

		# Only match by identifiers when we actually have one; an empty Q() would
		# match every trial and return an arbitrary, unrelated record.
		if query:
			trial = Trials.objects.filter(query).first()
			if trial:
				return trial

		# Fallback to title match (case-insensitive) — only merge when the candidate
		# does not conflict on a shared registry key (Option B guard).
		if title:
			candidate = Trials.objects.filter(title__iexact=title).first()
			if candidate and not identifiers_conflict(
				candidate.identifiers, clinical_trial.identifiers
			):
				self.log(f"Found trial by title: {candidate.title}", level=3)
				return candidate

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
					f"Created from Source: {source.name} ({source.source_id})"
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
					f"Added relationships Team: {source.team}  Subject:{source.subject}"
				)
				trial.save()
			return trial
		except IntegrityError as e:
			self.log(
				f"Integrity error during trial creation: {e}",
				level=3,
				style_func=self.style.ERROR,
			)

	def update_existing_trial(self, existing_trial, clinical_trial, source):
		"""Update an existing trial with new data only when necessary."""
		has_changes = False
		updated_fields = []  # Track which fields are updated

		# Update fields directly from ClinicalTrial object.
		# Only overwrite when the incoming value is non-empty so a sparse feed entry
		# never blanks a field a previous source populated (see docs/trials-multi-source-merge.md).
		if clinical_trial.title and existing_trial.title != clinical_trial.title:
			existing_trial.title = clinical_trial.title
			has_changes = True
			updated_fields.append("title")

		if clinical_trial.summary and existing_trial.summary != clinical_trial.summary:
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

		# Update identifiers
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

		# Update extra fields (if any exist in ClinicalTrial.extra_fields)
		extras = getattr(clinical_trial, "extra_fields", {})
		for field in [
			"therapeutic_areas",
			"country_status",
			"trial_region",
			"results_posted",
			"overall_decision_date",
			"countries_decision_date",
			"sponsor_type",
			"condition",
			"primary_outcome",
			"secondary_outcome",
			"primary_sponsor",
			"recruitment_status",
			"source_register",
		]:
			if (
				field in extras
				and extras[field] not in (None, "")
				and getattr(existing_trial, field) != extras[field]
			):
				setattr(existing_trial, field, extras[field])
				has_changes = True
				updated_fields.append(field)

		# Update WHO fields (if applicable and provided in ClinicalTrial.extra_fields)
		for who_field in [
			"scientific_title",
			"recruitment_status",
			"date_registration",
			"study_type",
			"phase",
			"countries",
			"inclusion_criteria",
			"exclusion_criteria",
			"intervention",
			"secondary_id",
			"inclusion_agemin",
			"inclusion_agemax",
			"inclusion_gender",
			"target_size",
			"last_refreshed_on",
		]:
			if (
				who_field in extras
				and extras[who_field] not in (None, "")
				and getattr(existing_trial, who_field) != extras[who_field]
			):
				setattr(existing_trial, who_field, extras[who_field])
				has_changes = True
				updated_fields.append(who_field)

		# Save only if changes were detected
		if has_changes:
			existing_trial._change_reason = safe_change_reason(
				f"Updated fields from {source.name} ({source.source_id}): {', '.join(updated_fields)}"
			)
			existing_trial.save()

		# Handle source, team, and subjects additions (relationships)
		if source.subject and source.subject not in existing_trial.subjects.all():
			existing_trial.subjects.add(source.subject)
			existing_trial._change_reason = safe_change_reason(
				f"Added subject: {source.subject}"
			)
			existing_trial.save()

		if source not in existing_trial.sources.all():
			existing_trial.sources.add(source)
			existing_trial._change_reason = safe_change_reason(
				f"Added new source: {source.name} ({source.source_id})"
			)
			existing_trial.save()

		if source.team and source.team not in existing_trial.teams.all():
			existing_trial.teams.add(source.team)
			existing_trial._change_reason = safe_change_reason(
				f"Added team: {source.team}"
			)
			existing_trial.save()
