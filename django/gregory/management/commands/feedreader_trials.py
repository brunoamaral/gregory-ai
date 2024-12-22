from django.core.management.base import BaseCommand
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone
from gregory.models import Trials, Sources
from gregory.classes import ClinicalTrial
from gregory.functions import remove_utm
from simple_history.utils import update_change_reason
from dateutil.parser import parse
import pytz
import re
import feedparser
import requests

class Command(BaseCommand):
	def handle(self, *args, **options):
		self.setup()
		self.update_trials_from_feeds()

	def setup(self):
		self.tzinfos = {
			"EDT": pytz.timezone("America/New_York"),
			"EST": pytz.timezone("America/New_York")
		}

	def update_trials_from_feeds(self):
		"""Fetch and process RSS feeds for clinical trials."""
		sources = Sources.objects.filter(method='rss', source_for='trials', active=True)
		for source in sources:
			feed = self.fetch_feed(source.link, source.ignore_ssl)
			for entry in feed['entries']:
				clinical_trial = self.process_feed_entry(entry, source)
				if clinical_trial:
					self.sync_clinical_trial(clinical_trial, source)

	def fetch_feed(self, link: str, ignore_ssl: bool):
		if not ignore_ssl:
			return feedparser.parse(link)
		else:
			response = requests.get(link, verify=False)
			return feedparser.parse(response.content)

	def process_feed_entry(self, entry: dict, source) -> ClinicalTrial:
		"""
		Process an RSS feed entry into a ClinicalTrial object.
		We attempt to get summary_html from 'summary_detail' first,
		and if it's empty, we fall back to 'summary'.
		"""
		summary_html = entry.get('summary_detail', {}).get('value', '') or entry.get('summary', '')
		published = self.parse_date(entry.get('published'))
		link = remove_utm(entry['link'])

		identifiers = self.extract_identifiers(link, entry.get('guid'))
		
		# If link is from euclinicaltrials.eu, parse EU data from summary_html
		extra_fields = {}
		if 'euclinicaltrials.eu' in link:
			extra_fields = self.parse_eu_clinical_trial_data(summary_html)
			# If we found a specific EudraCT/EUCT in parse_eu_clinical_trial_data, place it into identifiers
			if extra_fields.get('euct'):
				identifiers['euct'] = extra_fields['euct']

		return ClinicalTrial(
			title=entry['title'],
			summary=summary_html,
			link=link,
			published_date=published,
			identifiers=identifiers,
			extra_fields=extra_fields
		)

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
		eudract = re.search(r'eudract_number%3A(\d{4}-\d{6}-\d{2})', link)
		nct = guid if 'clinicaltrials.gov' in link else None
		return {
			"eudract": eudract.group(1) if eudract else None,
			"nct": nct,
			"euct": eudract.group(1) if eudract else None,
		}

	def parse_eu_clinical_trial_data(self, summary_html: str) -> dict:
		"""
		Extract relevant fields from euclinicaltrials.eu summary, including the official EudraCT ID.
		"""
		def _extract(pattern):
			match = re.search(pattern, summary_html, re.IGNORECASE)
			if not match:
				return None
			# Get the raw matched text
			raw_val = match.group(1)
			# Strip off any leading colon and whitespace
			raw_val = raw_val.lstrip(': ').strip()
			return raw_val

		# Specifically parse <b>Trial number</b>: 2023-xxxxxx-xx
		eudract_pattern = r'Trial number</b>:\s*([0-9]{4}-[0-9]{6}-[0-9]{2})'
		eudract_match = re.search(eudract_pattern, summary_html)
		euct = eudract_match.group(1) if eudract_match else None

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
			'euct': euct,  # <-- The EudraCT ID parsed from 'Trial number'
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

	def sync_clinical_trial(self, clinical_trial: ClinicalTrial, source):
		"""Sync a ClinicalTrial object with the database."""
		existing_trial = self.find_existing_trial(clinical_trial)
		if existing_trial:
			self.update_existing_trial(existing_trial, clinical_trial, source)
		else:
			self.create_new_trial(clinical_trial, source)

	def find_existing_trial(self, clinical_trial: ClinicalTrial):
		"""
		Only match by EudraCT (euct) if present. 
		If euct is None or we can't find a match, we consider it new.
		"""
		euct = clinical_trial.identifiers.get('euct')
		if euct:
			# If we have euct, try to find an existing trial with the same euct
			return Trials.objects.filter(identifiers__euct=euct).first()

		# No euct => treat as new
		return None

	def update_existing_trial(self, existing_trial, clinical_trial, source):
		"""Update an existing trial."""
		fields_to_watch = [
			'title', 'summary', 'link', 'published_date', 'identifiers',
			'therapeutic_areas', 'country_status', 'trial_region', 'results_posted',
			'overall_decision_date', 'countries_decision_date', 'sponsor_type'
		]
		initial_state = {field: getattr(existing_trial, field, None) for field in fields_to_watch}

		try:
			self.apply_trial_updates(existing_trial, clinical_trial, source)
		except IntegrityError:
			self.apply_trial_updates(existing_trial, clinical_trial, source, exclude_title=True)
		
		if any(initial_state[field] != getattr(existing_trial, field) for field in initial_state):
			update_change_reason(existing_trial, "Updated from RSS feed.")
			existing_trial.save()
			print(f"Trial {existing_trial.pk} updated.")
		else:
			print(f"No changes detected for Trial {existing_trial.pk}.")

	def apply_trial_updates(self, trial, clinical_trial, source, exclude_title=False):
		"""Apply updates to a trial."""
		if not exclude_title:
			trial.title = clinical_trial.title
		trial.summary = clinical_trial.summary
		trial.link = clinical_trial.link
		trial.published_date = clinical_trial.published_date
		trial.identifiers = clinical_trial.identifiers

		extras = getattr(clinical_trial, 'extra_fields', {})
		if extras:
			trial.therapeutic_areas = extras.get('therapeutic_areas')
			trial.country_status = extras.get('country_status')
			trial.trial_region = extras.get('trial_region')
			trial.results_posted = extras.get('results_posted', False)
			trial.overall_decision_date = extras.get('overall_decision_date')
			trial.countries_decision_date = extras.get('countries_decision_date')
			trial.sponsor_type = extras.get('sponsor_type')
			trial.condition = extras.get('condition')
			trial.recruitment_status = extras.get('recruitment_status')
			trial.primary_outcome = extras.get('primary_outcome')
			trial.secondary_outcome = extras.get('secondary_outcome')
			trial.primary_sponsor = extras.get('primary_sponsor')
			trial.results_posted = extras.get('results_posted')

		trial.save(
			update_fields=[
				'summary',
				'link',
				'published_date',
				'identifiers',
				'therapeutic_areas',
				'country_status',
				'trial_region',
				'results_posted',
				'overall_decision_date',
				'countries_decision_date',
				'sponsor_type',
				'condition',
				'recruitment_status',
				'primary_outcome',
				'secondary_outcome',
				'primary_sponsor',
				'results_posted',
			]
		)

		trial.sources.add(source)
		trial.teams.add(source.team)
		trial.subjects.add(source.subject)

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
			trial.sources.add(source)
			trial.teams.add(source.team)
			trial.subjects.add(source.subject)
			print(f"Created new trial: {trial.trial_id}")
		except IntegrityError as e:
			print(f"Integrity error during trial creation: {e}")