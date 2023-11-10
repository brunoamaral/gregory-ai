import datetime
from django.utils import timezone
from dateutil import tz
import xml.etree.ElementTree as ET
from gregory.models import Trials  # Replace with your actual model
from django.db import IntegrityError
from django.utils.dateparse import parse_datetime, parse_date
from dateutil.parser import parse
import re

def get_text(trial, tag_name):
    """
    Extract text from an XML element.
    """
    element = trial.find(tag_name)
    return element.text.strip() if element is not None and element.text is not None else None

def robust_parse_date(date_str):
    """
    Attempt to parse a date string into a timezone-aware date object, handling various formats.
    """
    if not date_str:
        return None

    try:
        naive_date = parse(date_str).date()
        # Assuming the date should be treated as UTC. Adjust if you have different timezone requirements.
        aware_datetime = timezone.make_aware(datetime.datetime.combine(naive_date, datetime.time(0, 0)), timezone.utc)
        return aware_datetime
    except ValueError:
        return None
def get_or_create_trial(trial_data):
    """
    Get or create a Trials record.
    """
    trial_id = trial_data.pop('trialid', None)
    existing_entry_by_title = Trials.objects.filter(title=trial_data['title']).first()

    # Update existing record found by title
    if existing_entry_by_title:
        if trial_id:
            # Update identifiers if trial_id is present
            match = re.match(r"([a-zA-Z-]+)([0-9]+)", trial_id, re.I)
            prefix = match.groups()[0].lower() if match else None
            # Remove trailing '-' if present
            if prefix and prefix.endswith('-'):
                prefix = prefix[:-1]
            trial_data['identifiers'] = {prefix: trial_id}
            if prefix:
                trial_data['identifiers'] = {prefix: trial_id}

        for key, value in trial_data.items():
            setattr(existing_entry_by_title, key, value)
        existing_entry_by_title.save()
        return

    # If no entry is found by title and trial_id is provided, check by trial_id
    if trial_id:
        match = re.match(r"([a-zA-Z-]+)([0-9]+)", trial_id, re.I)
        prefix = match.groups()[0].lower() if match else None
        if prefix:
            trial_data['identifiers'] = {prefix: trial_id}
            query = {f'identifiers__{prefix}': trial_id}
            existing_entry_by_id = Trials.objects.filter(**query).first()

            if existing_entry_by_id:
                for key, value in trial_data.items():
                    setattr(existing_entry_by_id, key, value)
                existing_entry_by_id.save()
                return

    # Create a new record if no existing record is found
    try:
        Trials.objects.create(**trial_data)
    except IntegrityError as e:
        print("Error occurred:", e)


def update_or_create_from_xml(xml_file_path):
    tree = ET.parse(xml_file_path)
    root = tree.getroot()

    for trial in root.findall('Trial'):
        trial_data = {}
        # Extract trial fields
        for field in ['Internal_Number', 'Last_Refreshed_on', 'Scientific_title', 'Primary_sponsor', 
                      'Retrospective_flag', 'Source_Register', 'Recruitment_Status', 'other_records', 
                      'Inclusion_agemin', 'Inclusion_agemax', 'Inclusion_gender', 'Target_size', 
                      'Study_type', 'Study_design', 'Phase', 'Countries', 'Contact_Firstname', 
                      'Contact_Lastname', 'Contact_Address', 'Contact_Email', 'Contact_Tel', 
                      'Contact_Affiliation', 'Inclusion_Criteria', 'Exclusion_Criteria', 'Condition', 
                      'Intervention', 'Primary_outcome', 'Secondary_outcome', 'Secondary_ID', 
                      'Source_Support', 'Ethics_review_status', 'Ethics_review_contact_name', 
                      'Ethics_review_contact_address', 'Ethics_review_contact_phone', 'Ethics_review_contact_email']:
            trial_data[field.lower()] = get_text(trial, field)

        # Special field mappings
        trial_data['title'] = get_text(trial, 'Public_title')
        trial_data['link'] = get_text(trial, 'web_address')
        trial_data['trialid'] = get_text(trial, 'TrialID')

        # Parse dates with robust_parse_date
        for date_field in ['Export_date', 'Date_enrollement', 'Ethics_review_approval_date', 
                           'results_date_completed', 'Last_Refreshed_on']:
            raw_date = get_text(trial, date_field)
            trial_data[date_field.lower()] = robust_parse_date(raw_date)

        # Mapping 'Date_registration' to 'published_date'
        date_registration_raw = get_text(trial, 'Date_registration')
        trial_data['published_date'] = robust_parse_date(date_registration_raw)

        get_or_create_trial(trial_data)

# Usage
# xml_file_path = 'path/to/your/xml/file.xml'
# update_or_create_from_xml(xml_file_path)