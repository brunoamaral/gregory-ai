import xml.etree.ElementTree as ET
from gregory.models import Trials  # Replace with your actual model
from django.db import IntegrityError
import re
from django.utils.dateparse import parse_datetime, parse_date

def update_or_create_from_xml(xml_file_path):
		tree = ET.parse(xml_file_path)
		root = tree.getroot()

		for trial in root.findall('Trial'):
				# Extracting required fields
				def get_text(tag_name):
						element = trial.find(tag_name)
						return element.text.strip() if element is not None and element.text is not None else None

				# Extract trial fields
				public_title = get_text('Public_title')
				trial_id = get_text('TrialID')
				# Extract and parse other fields
				export_date = parse_datetime(get_text('Export_date'))
				internal_number = get_text('Internal_Number')
				last_refreshed_on = parse_date(get_text('Last_Refreshed_on'))
				scientific_title = get_text('Scientific_title')
				primary_sponsor = get_text('Primary_sponsor')
				retrospective_flag = get_text('Retrospective_flag')
				date_registration_raw = get_text('Date_re_rawgistration')
				date_registration = parse_date(date_registration_raw) if date_registration_raw else None
				source_register = get_text('Source_Register')
				recruitment_status = get_text('Recruitment_Status')
				other_records = get_text('other_records')
				inclusion_agemin = get_text('Inclusion_agemin')
				inclusion_agemax = get_text('Inclusion_agemax')
				inclusion_gender = get_text('Inclusion_gender')
				date_enrollement_raw = get_text('Date_enrollement')
				date_enrollement = parse_date(date_enrollement_raw) if date_enrollement_raw else None
				target_size = get_text('Target_size')
				study_type = get_text('Study_type')
				study_design = get_text('Study_design')
				phase = get_text('Phase')
				countries = get_text('Countries')
				contact_firstname = get_text('Contact_Firstname')
				contact_lastname = get_text('Contact_Lastname')
				contact_address = get_text('Contact_Address')
				contact_email = get_text('Contact_Email')
				contact_tel = get_text('Contact_Tel')
				contact_affiliation = get_text('Contact_Affiliation')
				inclusion_criteria = get_text('Inclusion_Criteria')
				exclusion_criteria = get_text('Exclusion_Criteria')
				condition = get_text('Condition')
				intervention = get_text('Intervention')
				primary_outcome = get_text('Primary_outcome')
				secondary_outcome = get_text('Secondary_outcome')
				secondary_id = get_text('Secondary_ID')
				source_support = get_text('Source_Support')
				ethics_review_status = get_text('Ethics_review_status')
				ethics_review_approval_date_raw = get_text('Ethics_review_approval_date')
				ethics_review_approval_date = parse_date(ethics_review_approval_date_raw) if ethics_review_approval_date_raw else None
				ethics_review_contact_name = get_text('Ethics_review_contact_name')
				ethics_review_contact_address = get_text('Ethics_review_contact_address')
				ethics_review_contact_phone = get_text('Ethics_review_contact_phone')
				ethics_review_contact_email = get_text('Ethics_review_contact_email')
				results_date_completed_raw = get_text('results_date_completed')
				results_date_completed = parse_date(results_date_completed_raw) if results_date_completed_raw else None

				results_url_link = get_text('results_url_link')

				# Create the identifiers dictionary
				match = re.match(r"([a-zA-Z-]+)([0-9]+)", trial_id, re.I)
				prefix = match.groups()[0].lower() if match else None
				if prefix and prefix.endswith('-'): prefix = prefix[:-1]
				identifiers = {prefix: trial_id} if prefix else {}

				try:
						# Try creating a new record
						Trials.objects.create(
								title=public_title,
								identifiers=identifiers,
								export_date=export_date,
								internal_number=internal_number,
								last_refreshed_on=last_refreshed_on,
								scientific_title=scientific_title,
								primary_sponsor=primary_sponsor,
								retrospective_flag=retrospective_flag,
								date_registration=date_registration,
								source_register=source_register,
								recruitment_status=recruitment_status,
								other_records=other_records,
								inclusion_agemin=inclusion_agemin,
								inclusion_agemax=inclusion_agemax,
								inclusion_gender=inclusion_gender,
								date_enrollement=date_enrollement,
								target_size=target_size,
								study_type=study_type,
								study_design=study_design,
								phase=phase,
								countries=countries,
								contact_firstname=contact_firstname,
								contact_lastname=contact_lastname,
								contact_address=contact_address,
								contact_email=contact_email,
								contact_tel=contact_tel,
								contact_affiliation=contact_affiliation,
								inclusion_criteria=inclusion_criteria,
								exclusion_criteria=exclusion_criteria,
								condition=condition,
								intervention=intervention,
								primary_outcome=primary_outcome,
								secondary_outcome=secondary_outcome,
								secondary_id=secondary_id,
								source_support=source_support,
								ethics_review_status=ethics_review_status,
								ethics_review_approval_date=ethics_review_approval_date,
								ethics_review_contact_name=ethics_review_contact_name,
								ethics_review_contact_address=ethics_review_contact_address,
								ethics_review_contact_phone=ethics_review_contact_phone,
								ethics_review_contact_email=ethics_review_contact_email,
								results_date_completed = parse_date(results_date_completed_raw) if results_date_completed_raw else None,
								results_url_link=results_url_link,
						)
				except IntegrityError:
						# If there's a duplicate key error, find and update the existing record
						existing_entry = Trials.objects.get(title=public_title)
						existing_entry.identifiers = identifiers
						existing_entry.export_date = export_date
						existing_entry.internal_number = internal_number
						existing_entry.last_refreshed_on = last_refreshed_on
						existing_entry.scientific_title = scientific_title
						existing_entry.primary_sponsor = primary_sponsor
						existing_entry.retrospective_flag = retrospective_flag
						existing_entry.date_registration = date_registration
						existing_entry.source_register = source_register
						existing_entry.recruitment_status = recruitment_status
						existing_entry.other_records = other_records
						existing_entry.inclusion_agemin = inclusion_agemin
						existing_entry.inclusion_agemax = inclusion_agemax
						existing_entry.inclusion_gender = inclusion_gender
						existing_entry.date_enrollement = date_enrollement
						existing_entry.target_size = target_size
						existing_entry.study_type = study_type
						existing_entry.study_design = study_design
						existing_entry.phase = phase
						existing_entry.countries = countries
						existing_entry.contact_firstname = contact_firstname
						existing_entry.contact_lastname = contact_lastname
						existing_entry.contact_address = contact_address
						existing_entry.contact_email = contact_email
						existing_entry.contact_tel = contact_tel
						existing_entry.contact_affiliation = contact_affiliation
						existing_entry.inclusion_criteria = inclusion_criteria
						existing_entry.exclusion_criteria = exclusion_criteria
						existing_entry.condition = condition
						existing_entry.intervention = intervention
						existing_entry.primary_outcome = primary_outcome
						existing_entry.secondary_outcome = secondary_outcome
						existing_entry.secondary_id = secondary_id
						existing_entry.source_support = source_support
						existing_entry.ethics_review_status = ethics_review_status
						existing_entry.ethics_review_approval_date = ethics_review_approval_date
						existing_entry.ethics_review_contact_name = ethics_review_contact_name
						existing_entry.ethics_review_contact_address = ethics_review_contact_address
						existing_entry.ethics_review_contact_phone = ethics_review_contact_phone
						existing_entry.ethics_review_contact_email = ethics_review_contact_email
						existing_entry.results_date_completed = parse_date(results_date_completed_raw) if results_date_completed_raw else None
						existing_entry.results_url_link = results_url_link
						existing_entry.save()

# Usage
# xml_file_path = 'path/to/your/xml/file.xml'
# update_or_create_from_xml(xml_file_path)
