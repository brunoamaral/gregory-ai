from dateutil.parser import parse
from django.core.management.base import BaseCommand
from django.db import IntegrityError
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone
from gregory.classes import ClinicalTrial
from gregory.functions import remove_utm
from gregory.models import Trials, Sources
from simple_history.utils import update_change_reason
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
				# print(entry.title)
				summary_html = entry.get('summary_detail', {}).get('value', '') or entry.get('summary', '')
				published = self.parse_date(entry.get('published'))
				link = remove_utm(entry['link'])
				identifiers = self.extract_identifiers(link, entry.get('guid'))
				extra_fields = {}
				if 'euclinicaltrials.eu' in link:
					extra_fields = self.parse_eu_clinical_trial_data(summary_html)
				# self.stdout.write(self.style.NOTICE(f"Processing trial: : {link}\n {identifiers}"))
				clinical_trial = ClinicalTrial(
					title=entry['title'],
					summary=summary_html,
					link=link,
					published_date=published,
					identifiers=identifiers,
					extra_fields=extra_fields
				)

				existing_trial = self.find_existing_trial(clinical_trial)
				if existing_trial:
					self.update_existing_trial(existing_trial, clinical_trial, source)
					# self.stdout.write(self.style.SUCCESS(f"Trial already exists: {existing_trial}"))
					# self.update_existing_trial(existing_trial, clinical_trial, source)
					continue
				if not existing_trial:
					self.stdout.write(self.style.SUCCESS(f"Creating new trial: {clinical_trial.identifiers}"))
					new_trial = self.create_new_trial(clinical_trial, source)
					self.stdout.write((f"Creating new trial: {new_trial.identifiers}"))

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
		euct = re.search(r'EUCT=(\d{4}-\d{6}-\d{2})', link)
		return {
			"eudract": eudract.group(1) if eudract else None,
			"nct": nct,
			"euct": euct.group(1) if euct else None
		}

	def parse_eu_clinical_trial_data(self, summary_html: str) -> dict:
		"""Extract relevant fields from euclinicaltrials.eu summary."""
		def _extract(pattern):
			match = re.search(pattern, summary_html, re.IGNORECASE)
			return match.group(1).strip() if match else None

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
				trial.teams.add(source.team)
				trial.subjects.add(source.subject)
				trial.save()
		except IntegrityError as e:
			print(f"Integrity error during trial creation: {e}")

	def update_existing_trial(self, existing_trial, clinical_trial, source):
			"""Update an existing trial."""

			print(f"Updating trial with ID: {existing_trial.pk}")
			print(f"Identifiers before update: {existing_trial.identifiers}")
			merged_identifiers = self.merge_identifiers(existing_trial.identifiers, clinical_trial.identifiers)
			existing_trial.identifiers = merged_identifiers
			existing_trial.summary = clinical_trial.summary
			existing_trial.link = clinical_trial.link
			if source.subject not in existing_trial.subjects.all():
				existing_trial.subjects.add(source.subject)
				print(f"added subject {source.subject} to trial {existing_trial}")
			if source not in existing_trial.sources.all():
				existing_trial.sources.add(source)
				print(f"added source {source} to trial {existing_trial}")
			# Confirm history tracking
			try:
				update_change_reason(existing_trial, "Updated from RSS feed.")
			except AttributeError as e:
				print(f"History tracking failed: {e}")
				return 
			existing_trial.save()
	def merge_identifiers(self, existing_identifiers: dict, new_identifiers: dict) -> dict:
		"""Merge existing and new identifiers."""
		merged = existing_identifiers.copy() if existing_identifiers else {}
		for key, value in new_identifiers.items():
			if value and key not in merged:
				merged[key] = value
		return merged

