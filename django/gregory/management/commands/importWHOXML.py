from dateutil.parser import parse
from django.core.management.base import BaseCommand
from django.db import IntegrityError
from django.utils import timezone
from gregory.models import Trials, Sources
import datetime
import re
import xml.etree.ElementTree as ET

class Command(BaseCommand):
	help = 'Update or create trials from an XML file from https://trialsearch.who.int/Default.aspx'

	def add_arguments(self, parser):
			parser.add_argument('xml_file_path', type=str, help='The path to the XML file to import')

	def handle(self, *args, **options):
			xml_file_path = options['xml_file_path']
			self.update_or_create_from_xml(xml_file_path)
			self.stdout.write(self.style.SUCCESS('Successfully updated or created trials from XML'))

	def get_text(self, trial, tag_name):
			element = trial.find(tag_name)
			return element.text.strip() if element is not None and element.text is not None else None

	def robust_parse_date(self, date_str):
			if not date_str:
					return None

			try:
					naive_date = parse(date_str).date()
					aware_datetime = timezone.make_aware(datetime.datetime.combine(naive_date, datetime.time(0, 0)), datetime.timezone.utc)
					return aware_datetime
			except ValueError:
					return None

	def merge_identifiers(self, existing_identifiers, trial_id):
			if not isinstance(existing_identifiers, dict):
        # Handle the case where existing_identifiers is not a dictionary
				existing_identifiers = {}
			match = re.match(r"([a-zA-Z-]+)([0-9]+)", trial_id, re.I)
			prefix = match.groups()[0].lower() if match else None
			if prefix and prefix.endswith('-'):
					prefix = prefix[:-1]
			if prefix:
					existing_identifiers[prefix] = trial_id
			return existing_identifiers

	def get_or_create_trial(self, trial_data):
			trial_id = trial_data.pop('trialid', None)
			existing_entry_by_title = Trials.objects.filter(title=trial_data['title']).first()
			prefix = ''.join(filter(str.isalpha, trial_id)).lower() if trial_id else None
			source = Sources.objects.filter(name='WHO XML import').first()

			if existing_entry_by_title:
					if trial_id:
							existing_identifiers = existing_entry_by_title.identifiers or {}
							trial_data['identifiers'] = self.merge_identifiers(existing_identifiers, trial_id)

					for key, value in trial_data.items():
							setattr(existing_entry_by_title, key, value)
					existing_entry_by_title.save()
					return

			if trial_id:
					existing_entry_by_id = None
					existing_identifiers = {}
					trial_data['identifiers'] = self.merge_identifiers(existing_identifiers, trial_id)
					query = {f'identifiers__{prefix}': trial_id}
					existing_entry_by_id = Trials.objects.filter(**query).first()

					if existing_entry_by_id:
							for key, value in trial_data.items():
								setattr(existing_entry_by_id, key, value)
							existing_entry_by_id.save()
							return

			try:
					trial_data['discovery_date'] = timezone.now()  # Set the discovery date to current time
					new_trial = Trials.objects.create(**trial_data)
					new_trial.sources.add(source)
					new_trial.save()

			except IntegrityError as e:
					print("Error occurred:", e)

	def update_or_create_from_xml(self, xml_file_path):
			# Check if the source 'WHO XML import' exists
			source = Sources.objects.filter(name='WHO XML import').first()
			if not source:
				self.stdout.write(self.style.ERROR("Source 'WHO XML import' not found in the database. Please add it and try again."))
				return  # Stop the command if the source doesn't exist

			tree = ET.parse(xml_file_path)
			root = tree.getroot()

			for trial in root.findall('Trial'):
					trial_data = {}
					for field in ['Internal_Number', 'Last_Refreshed_on', 'Scientific_title', 'Primary_sponsor', 
					'Retrospective_flag', 'Source_Register', 'Recruitment_Status', 'other_records', 
					'Inclusion_agemin', 'Inclusion_agemax', 'Inclusion_gender', 'Target_size', 
					'Study_type', 'Study_design', 'Phase', 'Countries', 'Contact_Firstname', 
					'Contact_Lastname', 'Contact_Address', 'Contact_Email', 'Contact_Tel', 
					'Contact_Affiliation', 'Inclusion_Criteria', 'Exclusion_Criteria', 'Condition', 
					'Intervention', 'Primary_outcome', 'Secondary_outcome', 'Secondary_ID', 
					'Source_Support', 'Ethics_review_status', 'Ethics_review_contact_name', 
					'Ethics_review_contact_address', 'Ethics_review_contact_phone', 'Ethics_review_contact_email']:  # list of fields
							trial_data[field.lower()] = self.get_text(trial, field)

					trial_data['title'] = self.get_text(trial, 'Public_title')
					trial_data['link'] = self.get_text(trial, 'web_address')
					trial_data['trialid'] = self.get_text(trial, 'TrialID')

					for date_field in ['Export_date', 'Date_enrollement', 'Ethics_review_approval_date', 
						'results_date_completed', 'Last_Refreshed_on']:
							raw_date = self.get_text(trial, date_field)
							trial_data[date_field.lower()] = self.robust_parse_date(raw_date)

					date_registration_raw = self.get_text(trial, 'Date_registration')
					trial_data['published_date'] = self.robust_parse_date(date_registration_raw)

					self.get_or_create_trial(trial_data)


