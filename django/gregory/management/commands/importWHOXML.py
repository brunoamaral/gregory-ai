from dateutil.parser import parse
from django.core.management.base import BaseCommand
from django.db import IntegrityError
from django.utils import timezone
from gregory.models import Trials, Sources
from gregory.utils.trial_utils import identifiers_conflict, merge_links, canonical_link
import datetime
import re
import xml.etree.ElementTree as ET
import pytz
class Command(BaseCommand):
	help = 'Update or create trials from an XML file from https://trialsearch.who.int/Default.aspx'

	def add_arguments(self, parser):
		parser.add_argument('-f', '--file', dest='xml_file_path', type=str, required=True, help='The path to the XML file to import',)
		parser.add_argument('-s', '--source-id', type=int, required=True, help='ID of the source to associate with the trials',)

	def handle(self, *args, **options):
		xml_file_path = options['xml_file_path']
		source_id = options['source_id']
		self.parse_xml(xml_file_path, source_id)
		self.stdout.write(
			self.style.SUCCESS('Successfully updated or created trials from XML')
		)

	def get_text(self, trial, tag_name):
		element = trial.find(tag_name)
		if element is not None and element.text is not None:
			# Strip leading and trailing whitespace and normalize whitespace within
			return ' '.join(element.text.split()).strip()
		return None

	def _safe_change_reason(self, reason: str) -> str:
		"""Truncate change reason to fit within 100 character database limit."""
		return reason[:100] if len(reason) > 100 else reason

	def robust_parse_date(self, date_str):
		if not date_str:
			return None
		try:
			naive_date = parse(date_str).date()
			aware_datetime = timezone.make_aware(
				datetime.datetime.combine(naive_date, datetime.time(0, 0)), pytz.UTC
			)
			return aware_datetime
		except ValueError:
			return None

	def update_existing_trial(self, trial, trial_data, source, subject):
		has_changes = False
		updated_fields = []
		for key, value in trial_data.items():
			current_value = getattr(trial, key, None)

			# Handle datetime fields: Normalize and compare only the date part
			if key in ['export_date', 'date_enrollement', 'ethics_review_approval_date', 'results_date_completed', 'last_refreshed_on', 'date_registration']:
					if isinstance(current_value, datetime.datetime):
						current_date = current_value.date()
					elif isinstance(current_value, datetime.date):
						current_date = current_value
					else:
						current_date = None

					if isinstance(value, datetime.datetime):
						value_date = value.date()
					elif isinstance(value, datetime.date):
						value_date = value
					else:
						value_date = None

					# Only overwrite when the incoming date is present, so a missing XML
					# field never blanks a date a previous source populated
					# (see docs/trials-multi-source-merge.md).
					if value_date is not None and current_date != value_date:
						setattr(trial, key, value)
						has_changes = True
						updated_fields.append(key)

			# Handle other fields
			elif key == 'identifiers':
				# Conservative merge: preserve existing keys; only add keys that are
				# absent or null.  {**current, **value} would overwrite stored IDs on
				# every re-ingest (the "flip-flop" described in
				# docs/trials-identity-dedup.md).
				if isinstance(current_value, dict) and isinstance(value, dict):
					merged_identifiers = current_value.copy()
					for k, v in value.items():
						if v and (k not in merged_identifiers or merged_identifiers[k] is None):
							merged_identifiers[k] = v
					if merged_identifiers != current_value:
						trial.identifiers = merged_identifiers
						has_changes = True
						updated_fields.append(key)
				elif current_value != value:  # In case of non-dict values, simply set
					trial.identifiers = value
					has_changes = True
					updated_fields.append(key)
			elif key == 'link':
				# Record the WHO-exported registry URL under its registry key. The
				# canonical link is the first registry URL stored, chronologically —
				# it is never replaced, except to upgrade an aggregator (WHO ICTRP)
				# URL to a registry of record (see docs/trials-multi-source-merge.md).
				if value not in (None, ''):
					merged_links = merge_links(trial.links, value)
					if merged_links != (trial.links or {}):
						trial.links = merged_links
						has_changes = True
						updated_fields.append('links')
					new_link = canonical_link(trial.links, trial.link)
					if new_link and trial.link != new_link:
						trial.link = new_link
						has_changes = True
						updated_fields.append('link')
			# Only overwrite when the incoming value is non-empty, so a missing XML
			# field never blanks data a previous source populated.
			elif value not in (None, '') and current_value != value:
				setattr(trial, key, value)
				has_changes = True
				updated_fields.append(key)

		if has_changes:
			trial._change_reason = self._safe_change_reason(f"Updated fields from {source.name} ({source.source_id}): {', '.join(updated_fields)}")
			self.stdout.write(f"Saving changes for trial: {trial.trial_id}. Changes: {updated_fields}")
			trial.save()
		if source not in trial.sources.all():
			trial.sources.add(source)
			updated_fields.append(f"source: {source.name}")

		if subject not in trial.subjects.all():
			trial.subjects.add(subject)
			updated_fields.append(f"subject: {subject}")
		
	def create_new_trial(self, trial_data, source, subject):
		try:
			trial_data['discovery_date'] = timezone.now()
			if trial_data.get('link'):
				trial_data['links'] = merge_links(None, trial_data['link'])
			trial = Trials.objects.create(**trial_data)
			trial.sources.add(source)
			trial.subjects.add(subject)
			trial.teams.add(source.team)
			trial._change_reason = self._safe_change_reason(f"Created from Source: {source.name} ({source.source_id})")
			trial.save()
			return trial
		except IntegrityError as e:
			self.stdout.write(self.style.ERROR(f"Error creating trial: {trial_data.get('title', 'Unknown')}. Error: {e}"))
			return None

	def check_for_existing_trial(self, trial_data, source, subject):
		existing_trial = None

		# Step 1: Match by trial identifier in JSON field
		if 'identifiers' in trial_data and trial_data['identifiers']:
			# Extract the key dynamically from the first letters of the trialid value
			trial_id_value = list(trial_data['identifiers'].values())[0]  # Extract the trialid value
			identifier_key = list(trial_data['identifiers'].keys())[0]   # Extract the identifier key
				
				# Try to match the trial identifier with the key-value pair in the 'identifiers' JSON field
			if identifier_key:
				existing_trial = Trials.objects.filter(
					identifiers__contains={identifier_key: trial_id_value}
				).first()
				# Apply conflict guard: a value-level match under a different key should
				# not merge records that disagree on a shared registry key.
				if existing_trial and identifiers_conflict(existing_trial.identifiers, trial_data.get('identifiers')):
					existing_trial = None
			
			# (No broad value-only fallback here: on a JSONField,
			# `identifiers__contains=<scalar>` expects a JSON object/array, so with a
			# scalar id it never matched — it was a no-op. A value-level search across
			# arbitrary keys would also reintroduce the weak cross-key matches this
			# change is designed to avoid; reliable matching is the exact key:value
			# match above plus the guarded title fallback below.)
		# Step 2: Fallback to matching by title (case-insensitive) — only merge when
		# the candidate does not conflict on a shared registry key (Option B guard).
		if not existing_trial and 'title' in trial_data:
			candidate = Trials.objects.filter(title__iexact=trial_data['title']).first()
			if candidate and not identifiers_conflict(candidate.identifiers, trial_data.get('identifiers')):
				existing_trial = candidate
		# Step 3: Handle trial creation or update
		try:
			if existing_trial:
				self.update_existing_trial(existing_trial, trial_data, source, subject)
			else:
				# The old title-duplicate guard that silently skipped creation has been
				# removed: the conflict guard above already decides these are different
				# trials, and the per-registry partial unique indexes (migration 0054)
				# enforce true uniqueness — not the title constraint.
				self.create_new_trial(trial_data, source, subject)
		except IntegrityError as e:
			self.stdout.write(
				self.style.ERROR(
					f"Error processing trial: {trial_data.get('title', 'Unknown')}. Error: {e}"
				)
			)

	def parse_xml(self, xml_file_path, source_id):
		try:
			source = Sources.objects.get(pk=source_id)
		except Sources.DoesNotExist:
			self.stdout.write(self.style.ERROR(f"Source with ID {source_id} not found."))
			return

		subject = source.subject
		if subject is None:
			self.stdout.write(self.style.ERROR(f"Source {source_id} has no associated subject."))
			return

		tree = ET.parse(xml_file_path)
		root = tree.getroot()

		for trial in root.findall('Trial'):
			trial_data = {}
			trial_identifier = self.get_text(trial, 'TrialID')
			# Sanitize and validate TrialID
			if trial_identifier:
				trial_identifier = trial_identifier.replace('\n', '').strip()
				key = ''.join(filter(str.isalpha, trial_identifier.split('-')[0])).lower()
				trial_data['identifiers'] = { key : trial_identifier}
			if not trial_identifier:
				self.stdout.write(self.style.WARNING(f"Missing or invalid TrialID for trial: {self.get_text(trial, 'Public_title')}. Skipping."))
				continue
			
			for field in [
				'Internal_Number', 'Last_Refreshed_on', 'Scientific_title', 'Primary_sponsor',
				'Prospective_registration', 'Source_Register', 'Recruitment_Status', 'other_records',
				'Inclusion_agemin', 'Inclusion_agemax', 'Inclusion_gender', 'Target_size',
				'Study_type', 'Study_design', 'Phase', 'Countries', 'Contact_Firstname',
				'Contact_Lastname', 'Contact_Address', 'Contact_Email', 'Contact_Tel',
				'Contact_Affiliation', 'Inclusion_Criteria', 'Exclusion_Criteria', 'Condition',
				'Intervention', 'Primary_outcome', 'Secondary_outcome', 'Secondary_ID',
				'Source_Support', 'Ethics_review_status', 'Ethics_review_contact_name',
				'Ethics_review_contact_address', 'Ethics_review_contact_phone', 'Ethics_review_contact_email',
				'Acronym', 'Secondary_Sponsor', 'results_yes_no', 'results_ipd_plan', 'results_ipd_description'
			]:
				trial_data[field.lower()] = self.get_text(trial, field)

			title = self.get_text(trial, 'Public_title')
			trial_data['title'] = title.replace('\n', ' ').replace('\r', ' ') if title else None
			trial_data['link'] = self.get_text(trial, 'web_address')

			for date_field in [
				'Export_date', 'Date_enrollement', 'Ethics_review_approval_date',
				'results_date_completed', 'Last_Refreshed_on'
			]:
				raw_date = self.get_text(trial, date_field)
				trial_data[date_field.lower()] = self.robust_parse_date(raw_date)

			date_registration_raw = self.get_text(trial, 'Date_registration')
			parsed_registration = self.robust_parse_date(date_registration_raw)
			# WHO ICTRP only provides a single "Date of registration"; mirror it into both
			# published_date (used across the app) and date_registration (registry field).
			trial_data['published_date'] = parsed_registration
			trial_data['date_registration'] = parsed_registration

			# Add logging for each trial being processed
			self.stdout.write(self.style.NOTICE(f"Processing trial: {trial_data['title']} with TrialID: {trial_data['identifiers']}"))

			try:
				self.check_for_existing_trial(trial_data, source, subject)
			except Exception as e:
				self.stdout.write(
					self.style.ERROR(
						f"Error importing trial '{trial_data.get('title', 'Unknown')}': {e}"
					)
				)