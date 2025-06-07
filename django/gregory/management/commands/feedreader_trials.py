from dateutil.parser import parse
from django.core.management.base import BaseCommand
from django.db import IntegrityError
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone
from gregory.classes import ClinicalTrial
from gregory.functions import remove_utm
from gregory.models import Trials, Sources
import feedparser
import pytz
import re
import requests

class Command(BaseCommand):
	def handle(self, *args, **options):
		self.setup()
		self.process_feeds()

	def setup(self):
		self.tzinfos = {
			"EDT": pytz.timezone("America/New_York"),
			"EST": pytz.timezone("America/New_York")
		}

	def _safe_change_reason(self, reason: str) -> str:
		"""Truncate change reason to fit within 100 character database limit."""
		return reason[:100] if len(reason) > 100 else reason

	def process_feeds(self):
		"""Fetch and process RSS feeds for clinical trials."""
		sources = Sources.objects.filter(method='rss', source_for='trials', active=True)
		for source in sources:
			self.stdout.write(self.style.SUCCESS(f"Processing RSS feed: {source.name}"))
			if not source.ignore_ssl:
				feed = feedparser.parse(source.link)
			else:
				response = requests.get(source.link, verify=False)
				feed = feedparser.parse(response.content)
			for entry in feed['entries']:
				try:
					# Extract trial details
					summary_html = entry.get('summary_detail', {}).get('value', '') or entry.get('summary', '')
					published = self.parse_date(entry.get('published'))
					link = remove_utm(entry['link'])
					identifiers = self.extract_identifiers(link, entry.get('guid'))
					extra_fields = {}
					if 'euclinicaltrials.eu' in link:
						extra_fields = self.parse_eu_clinical_trial_data(summary_html)

					# Create ClinicalTrial object
					incoming_clinical_trial = ClinicalTrial(
						title=entry['title'],
						summary=summary_html,
						link=link,
						published_date=published,
						identifiers=identifiers,
						extra_fields=extra_fields
					)

					# Check for existing trial
					existing_trial = self.find_existing_trial(incoming_clinical_trial)
					if existing_trial:
						self.update_existing_trial(existing_trial, incoming_clinical_trial, source)
						self.stdout.write(self.style.SUCCESS(f"Updated existing trial: {existing_trial.title}"))
						continue

					# Create new trial if no existing trial is found
					self.create_new_trial(incoming_clinical_trial, source)
					self.stdout.write(self.style.SUCCESS(f"Created new trial: {incoming_clinical_trial.title}"))
				
				except IntegrityError as e:
					self.stdout.write(self.style.ERROR(f"IntegrityError for trial '{entry.get('title')}' at link {link}: {e}"))
				except Exception as e:
					self.stdout.write(self.style.ERROR(f"Error processing trial '{entry.get('title')}' at link {link}: {e}"))

			self.stdout.write(self.style.SUCCESS(f"Finished processing RSS feed: {source.name}"))


	def parse_date(self, date_str: str):
		"""Parse a date string into a timezone-aware datetime."""
		if not date_str:
			return None
		return parse(date_str, tzinfos=self.tzinfos).astimezone(pytz.utc)

	def extract_identifiers(self, link: str, guid: str) -> dict:
		"""
		Extract identifiers from the link or guid.
		eudract/euct might come from the link if present,
		nct from guid if 'clinicaltrials.gov' is in the link.
		However, for euclinicaltrials.eu, we now rely on parse_eu_clinical_trial_data.
		"""
		eudract = re.search(r'(?:eudract_number%3A|EUDRACT=)(\d{4}-\d{6}-\d{2}-\d{2})', link, re.IGNORECASE)
		euct = re.search(r'(?:EUCT=)(\d{4}-\d{6}-\d{2}-\d{2})', link, re.IGNORECASE)
		nct = guid if 'clinicaltrials.gov' in link else None
		return {
			"eudract": eudract.group(1) if eudract else None,
			"nct": nct,
			"euct": euct.group(1) if euct else None
		}

	def parse_eu_clinical_trial_data(self, summary_html: str) -> dict:
		"""Extract relevant fields from euclinicaltrials.eu summary."""
		def _extract(pattern):
			match = re.search(pattern, summary_html, re.IGNORECASE)
			if not match:
				return None
			# Get the raw matched text
			raw_val = match.group(1)
			# Strip off any leading colon and whitespace
			raw_val = raw_val.lstrip(': ').strip()
			return raw_val
		eudract_pattern = r'Trial number</b>:\s*([0-9]{4}-[0-9]{6}-[0-9]{2})'
		therapeutic_areas = _extract(r'Therapeutic Areas[^>]*>([^<]+)')
		country_status = _extract(r'Status in each country[^>]*>([^<]+)')
		trial_region = _extract(r'Trial region[^>]*>([^<]+)')
		results_posted_str = _extract(r'Results posted[^>]*>([^<]+)')
		results_posted = (results_posted_str.lower() == 'yes') if results_posted_str else False
		medical_conditions = _extract(r'Medical conditions[^>]*>([^<]+)')
		overall_status = _extract(r'Overall trial status[^>]*>([^<]+)')
		primary_end_point = _extract(r'Primary end point[^>]*>([^<]+)')
		secondary_end_point = _extract(r'Secondary end point[^>]*>([^<]+)')
		overall_decision_date_str = _extract(r'Overall decision date[^>]*>([^<]+)')
		countries_decision_date_str = _extract(r'Countries decision date[^>]*>([^<]+)')
		sponsor = _extract(r'Sponsor[^>]*>([^<]+)')
		sponsor_type = _extract(r'Sponsor type[^>]*>([^<]+)')
		overall_decision_date = None
		if overall_decision_date_str:
			try:
				overall_decision_date = parse(overall_decision_date_str).date()
			except:
				pass

		countries_decision_date = {}
		if countries_decision_date_str:
			chunks = re.split(r'[;,]', countries_decision_date_str)
			for chunk in chunks:
				chunk_parts = chunk.strip().split(':')
				if len(chunk_parts) == 2:
					country_code = chunk_parts[0].strip()
					date_val = chunk_parts[1].strip()
					try:
						countries_decision_date[country_code] = str(parse(date_val).date())
					except:
						countries_decision_date[country_code] = date_val
		return {
			'condition': medical_conditions, 
			'primary_sponsor': sponsor,  
			'primary_outcome': primary_end_point,
			'secondary_outcome': secondary_end_point,
			'therapeutic_areas': therapeutic_areas,
			'country_status': country_status,
			'trial_region': trial_region,
			'results_posted': results_posted,
			'overall_decision_date': overall_decision_date,
			'countries_decision_date': countries_decision_date if countries_decision_date else None,
			'sponsor_type': sponsor_type,
			'results_posted': results_posted,
		}

	def find_existing_trial(self, clinical_trial: ClinicalTrial):
		identifiers = clinical_trial.identifiers
		title = clinical_trial.title.lower() if clinical_trial.title else None

		query = Q()
		if identifiers.get('euct'):
			query |= Q(identifiers__euct=identifiers['euct'])
		if identifiers.get('nct'):
			query |= Q(identifiers__nct=identifiers['nct'])
		if identifiers.get('ctis'):
			query |= Q(identifiers__ctis=identifiers['ctis'])

		trial = Trials.objects.filter(query).first()
		if trial:
			return trial

		if title:
			trial = Trials.objects.filter(title__iexact=title).first()
			if trial:
				print(f"Found trial by title: {trial.title}")
				return trial

	def create_new_trial(self, clinical_trial: ClinicalTrial, source):
		"""Create a new trial in the database."""
		extras = getattr(clinical_trial, 'extra_fields', {})
		try:
			trial = Trials.objects.create(
				discovery_date=timezone.now(),
				title=clinical_trial.title,
				summary=clinical_trial.summary,
				link=clinical_trial.link,
				published_date=clinical_trial.published_date,
				identifiers=clinical_trial.identifiers,
				therapeutic_areas=extras.get('therapeutic_areas'),
				country_status=extras.get('country_status'),
				trial_region=extras.get('trial_region'),
				results_posted=extras.get('results_posted', False),
				overall_decision_date=extras.get('overall_decision_date'),
				countries_decision_date=extras.get('countries_decision_date'),
				sponsor_type=extras.get('sponsor_type'),
				condition=extras.get('condition'),
				recruitment_status=extras.get('recruitment_status'),
				primary_outcome=extras.get('primary_outcome'),
				secondary_outcome=extras.get('secondary_outcome'),
				primary_sponsor=extras.get('primary_sponsor'),
			)
			if trial:
				trial.sources.add(source)
				trial._change_reason = self._safe_change_reason(f"Created from Source: {source.name} ({source.source_id})")
				trial.save()
				trial.teams.add(source.team)
				trial.subjects.add(source.subject)
				trial._change_reason = self._safe_change_reason(f"Added relationships Team: {source.team}  Subject:{source.subject}")
				trial.save()
			return trial
		except IntegrityError as e:
			print(f"Integrity error during trial creation: {e}")

	def update_existing_trial(self, existing_trial, clinical_trial, source):
		"""Update an existing trial with new data only when necessary."""
		has_changes = False
		updated_fields = []  # Track which fields are updated

		# Update fields directly from ClinicalTrial object
		if existing_trial.title != clinical_trial.title:
			existing_trial.title = clinical_trial.title
			has_changes = True
			updated_fields.append('title')

		if existing_trial.summary != clinical_trial.summary:
			existing_trial.summary = clinical_trial.summary
			has_changes = True
			updated_fields.append('summary')

		if existing_trial.link != clinical_trial.link:
			existing_trial.link = clinical_trial.link
			has_changes = True
			updated_fields.append('link')

		if existing_trial.published_date != clinical_trial.published_date:
			existing_trial.published_date = clinical_trial.published_date
			has_changes = True
			updated_fields.append('published_date')

		# Update identifiers
		merged_identifiers = self.merge_identifiers(existing_trial.identifiers, clinical_trial.identifiers)
		if merged_identifiers != existing_trial.identifiers:
			existing_trial.identifiers = merged_identifiers
			has_changes = True
			updated_fields.append('identifiers')

		# Update extra fields (if any exist in ClinicalTrial.extra_fields)
		extras = getattr(clinical_trial, 'extra_fields', {})
		for field in [
			'therapeutic_areas', 'country_status', 'trial_region', 'results_posted',
			'overall_decision_date', 'countries_decision_date', 'sponsor_type',
			'condition', 'primary_outcome', 'secondary_outcome', 'primary_sponsor',
			'recruitment_status'
		]:
			if field in extras and getattr(existing_trial, field) != extras[field]:
				setattr(existing_trial, field, extras[field])
				has_changes = True
				updated_fields.append(field)

		# Update WHO fields (if applicable and provided in ClinicalTrial.extra_fields)
		for who_field in [
			'scientific_title', 'recruitment_status', 'date_registration',
			'study_type', 'phase', 'countries', 'inclusion_criteria',
			'exclusion_criteria', 'intervention', 'secondary_id'
		]:
			if who_field in extras and getattr(existing_trial, who_field) != extras[who_field]:
				setattr(existing_trial, who_field, extras[who_field])
				has_changes = True
				updated_fields.append(who_field)

		# Save only if changes were detected
		if has_changes:
			existing_trial._change_reason = self._safe_change_reason(f"Updated fields from {source.name} ({source.source_id}): {', '.join(updated_fields)}")
			existing_trial.save()

		# Handle source and subjects additions (relationships)
		if source.subject not in existing_trial.subjects.all():
			existing_trial.subjects.add(source.subject)
			existing_trial._change_reason = self._safe_change_reason(f"Added subject: {source.subject}")
			existing_trial.save()

		if source not in existing_trial.sources.all():
			existing_trial.sources.add(source)
			existing_trial._change_reason = self._safe_change_reason(f"Added new source: {source.name} ({source.source_id})")
			existing_trial.save()
		
	def merge_identifiers(self, existing_identifiers: dict, new_identifiers: dict) -> dict:
			"""Merge existing and new identifiers."""
			merged = existing_identifiers.copy() if existing_identifiers else {}
			for key, value in new_identifiers.items():
				if value and (key not in merged or merged[key] is None):
					merged[key] = value
			return merged