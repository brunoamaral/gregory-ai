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
		self.tzinfos = {"EDT": pytz.timezone("America/New_York"), "EST": pytz.timezone("America/New_York")}

	def update_trials_from_feeds(self):
		"""Fetch and process RSS feeds for clinical trials."""
		sources = Sources.objects.filter(method='rss', source_for='trials')
		for source in sources:
			feed = self.fetch_feed(source.link, source.ignore_ssl)
			for entry in feed['entries']:
				clinical_trial = self.process_feed_entry(entry, source)
				if clinical_trial:
					self.sync_clinical_trial(clinical_trial, source)

	def fetch_feed(self, link: str, ignore_ssl: bool):
		"""Fetch the RSS feed for a given source."""
		if not ignore_ssl:
			return feedparser.parse(link)
		else:
			response = requests.get(link, verify=False)
			return feedparser.parse(response.content)

	def process_feed_entry(self, entry: dict, source) -> ClinicalTrial:
		"""Process an RSS feed entry into a ClinicalTrial object."""
		summary = entry.get('summary', entry.get('summary_detail', {}).get('value', ''))
		published = self.parse_date(entry.get('published'))
		link = remove_utm(entry['link'])

		identifiers = self.extract_identifiers(link, entry.get('guid'))
		return ClinicalTrial(
			title=entry['title'],
			summary=summary,
			link=link,
			published_date=published,
			identifiers=identifiers,
		)

	def parse_date(self, date_str: str):
		"""Parse a date string into a timezone-aware datetime."""
		if not date_str:
			return None
		return parse(date_str, tzinfos=self.tzinfos).astimezone(pytz.utc)

	def extract_identifiers(self, link: str, guid: str) -> dict:
		"""Extract identifiers (e.g., NCT, EudraCT) from the link or GUID."""
		eudract = re.search(r'eudract_number%3A(\d{4}-\d{6}-\d{2})', link)
		nct = guid if 'clinicaltrials.gov' in link else None
		return {
			"eudract": eudract.group(1) if eudract else None,
			"nct": nct,
			"euct": eudract.group(1) if eudract else None,
		}

	def sync_clinical_trial(self, clinical_trial: ClinicalTrial, source):
		"""Sync a ClinicalTrial object with the database."""
		existing_trial = self.find_existing_trial(clinical_trial)

		if existing_trial:
			self.update_existing_trial(existing_trial, clinical_trial, source)
		else:
			self.create_new_trial(clinical_trial, source)

	def find_existing_trial(self, clinical_trial: ClinicalTrial):
		"""Find an existing trial by identifiers or title."""
		query = Q()
		for key, value in clinical_trial.identifiers.items():
			if value:
				query |= Q(**{f'identifiers__{key}': value})
		return Trials.objects.filter(query).first() or Trials.objects.filter(title=clinical_trial.title).first()

	def update_existing_trial(self, existing_trial, clinical_trial, source):
		"""Update an existing trial."""
		initial_state = {field: getattr(existing_trial, field) for field in ['title', 'summary', 'link', 'published_date', 'identifiers']}
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
		trial.save(update_fields=['summary', 'link', 'published_date', 'identifiers'])

		# Update Many-to-Many relationships
		trial.sources.add(source)
		trial.teams.add(source.team)
		trial.subjects.add(source.subject)

	def create_new_trial(self, clinical_trial: ClinicalTrial, source):
		"""Create a new trial in the database."""
		try:
			trial = Trials.objects.create(
				discovery_date=timezone.now(),
				title=clinical_trial.title,
				summary=clinical_trial.summary,
				link=clinical_trial.link,
				published_date=clinical_trial.published_date,
				identifiers=clinical_trial.identifiers,
			)
			# Save first, then update Many-to-Many relationships
			trial.save()
			trial.sources.add(source)
			trial.teams.add(source.team)
			trial.subjects.add(source.subject)
			print(f"Created new trial: {trial.trial_id}")
		except IntegrityError as e:
			print(f"Integrity error during trial creation: {e}")