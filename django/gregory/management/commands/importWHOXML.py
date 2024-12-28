from dateutil.parser import parse
from django.core.management.base import BaseCommand
from django.db import IntegrityError
from django.utils import timezone
from gregory.models import Trials, Sources
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
		self.update_or_create_from_xml(xml_file_path, source_id)
		self.stdout.write(
			self.style.SUCCESS('Successfully updated or created trials from XML')
		)

	def get_text(self, trial, tag_name):
		element = trial.find(tag_name)
		return (
			element.text.strip()
			if element is not None and element.text is not None
			else None
		)

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
			if key in ['export_date', 'date_enrollement', 'ethics_review_approval_date', 'results_date_completed', 'last_refreshed_on']:
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

					if current_date != value_date:  # Compare only the date part
							# self.stdout.write(f"Updating datetime field '{key}': {current_date} -> {value_date}")
							setattr(trial, key, value)
							has_changes = True
							updated_fields.append(key)

			# Handle other fields
			elif current_value != value:
					# self.stdout.write(f"Updating field '{key}': {current_value} -> {value}")
					setattr(trial, key, value)
					has_changes = True
					updated_fields.append(key)

		# Ensure source is added only if not already associated
		if source not in trial.sources.all():
				trial.sources.add(source)
				updated_fields.append(f"source: {source.name}")

		# Ensure subject is added only if not already associated
		if subject not in trial.subjects.all():
				trial.subjects.add(subject)
				updated_fields.append(f"subject: {subject}")

		# Save the trial only if there are actual changes
		if has_changes:
				trial._change_reason = f"Updated fields: {', '.join(updated_fields)}"
				# self.stdout.write(f"Saving changes for trial: {trial.trial_id}. Changes: {updated_fields}")
				trial.save()

	def create_new_trial(self, trial_data, source, subject):
		try:
			trial_data['discovery_date'] = timezone.now()
			trial = Trials.objects.create(**trial_data)
			trial.sources.add(source)
			trial.subjects.add(subject)
			trial.teams.add(source.team)
			trial.identifiers = {
				''.join(filter(str.isalpha, trial_data['trialid'])).lower(): trial_data['trialid']
			}
			trial._change_reason = f"Created trial from source: {source.name}, team: {source.team}, with subject: {subject}"
			trial.save()
			return trial
		except IntegrityError as e:
			self.stdout.write(self.style.ERROR(f"Error creating trial: {trial_data.get('title', 'Unknown')}. Error: {e}"))
			return None

	def get_or_create_trial(self, trial_data, source, subject):
		trial_identifier = trial_data.pop('trialid', None)
		existing_trial = None

		# Step 1: Match by trial identifier in JSON field
		if trial_identifier:
			# Extract the key dynamically from the first letters of the value
			identifier_key = ''.join(filter(str.isalpha, trial_identifier.split("-")[0])).lower()

			# Try to match the trial identifier with the key-value pair in the 'identifiers' JSON field
			if identifier_key:
				existing_trial = Trials.objects.filter(
					identifiers__contains={identifier_key: trial_identifier}
				).first()
				# print(f"Existing trial by identifier key-value pair: {existing_trial}")

			# Fallback to a broader search if no specific key match
			if not existing_trial:
				existing_trial = Trials.objects.filter(
					identifiers__icontains=trial_identifier
				).first()

		# Step 2: Fallback to matching by title (case-insensitive)
		if not existing_trial and 'title' in trial_data:
			existing_trial = Trials.objects.filter(title__iexact=trial_data['title']).first()
			# print(f"Existing trial by title: {existing_trial}")
		# Step 3: Handle trial creation or update
		try:
			if existing_trial:
				self.update_existing_trial(existing_trial, trial_data, source, subject)
			else:
				# Check for duplicate titles one last time before creating a trial
				duplicate_trial = Trials.objects.filter(title__iexact=trial_data['title']).exists()
				if duplicate_trial:
						self.stdout.write(
								self.style.WARNING(
									f"Duplicate trial title found (case-insensitive): {trial_data['title']}. Skipping."
								)
						)
						return None

				# Create a new trial
				trial_data['discovery_date'] = timezone.now()
				trial = Trials.objects.create(**trial_data)
				trial.sources.add(source)
				trial.subjects.add(subject)
				trial.teams.add(source.team)
				trial.identifiers = {
					''.join(filter(str.isalpha, trial_data['trialid'])).lower(): trial_data['trialid']
				}

				trial._change_reason = f"Created trial from source: {source.name}, team: {source.team}, with subject: {subject}"
				trial.save()
		except IntegrityError as e:
			self.stdout.write(
				self.style.ERROR(
					f"Error processing trial: {trial_data.get('title', 'Unknown')}. Error: {e}"
				)
			)

	def update_or_create_from_xml(self, xml_file_path, source_id):
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
			for field in [
				'Internal_Number', 'Last_Refreshed_on', 'Scientific_title', 'Primary_sponsor',
				'Retrospective_flag', 'Source_Register', 'Recruitment_Status', 'other_records',
				'Inclusion_agemin', 'Inclusion_agemax', 'Inclusion_gender', 'Target_size',
				'Study_type', 'Study_design', 'Phase', 'Countries', 'Contact_Firstname',
				'Contact_Lastname', 'Contact_Address', 'Contact_Email', 'Contact_Tel',
				'Contact_Affiliation', 'Inclusion_Criteria', 'Exclusion_Criteria', 'Condition',
				'Intervention', 'Primary_outcome', 'Secondary_outcome', 'Secondary_ID',
				'Source_Support', 'Ethics_review_status', 'Ethics_review_contact_name',
				'Ethics_review_contact_address', 'Ethics_review_contact_phone', 'Ethics_review_contact_email'
			]:
				trial_data[field.lower()] = self.get_text(trial, field)

			title = self.get_text(trial, 'Public_title')
			trial_data['title'] = title.replace('\n', ' ').replace('\r', ' ') if title else None
			trial_data['link'] = self.get_text(trial, 'web_address')
			trial_data['trialid'] = self.get_text(trial, 'TrialID')

			for date_field in [
				'Export_date', 'Date_enrollement', 'Ethics_review_approval_date',
				'results_date_completed', 'Last_Refreshed_on'
			]:
				raw_date = self.get_text(trial, date_field)
				trial_data[date_field.lower()] = self.robust_parse_date(raw_date)

			date_registration_raw = self.get_text(trial, 'Date_registration')
			trial_data['published_date'] = self.robust_parse_date(date_registration_raw)

			# Add logging for each trial being processed
			# self.stdout.write(self.style.NOTICE(f"Processing trial: {trial_data['title']}"))

			try:
				self.get_or_create_trial(trial_data, source, subject)
			except Exception as e:
				self.stdout.write(
					self.style.ERROR(
						f"Error importing trial '{trial_data.get('title', 'Unknown')}': {e}"
					)
				)