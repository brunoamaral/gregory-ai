from django.core.management.base import BaseCommand
from gregory.models import Articles, Trials, Sources, Authors
from crossref.restful import Works, Etiquette
from dateutil.parser import parse
from dateutil.tz import gettz
from django.core.exceptions import MultipleObjectsReturned
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone
from gregory.classes import SciencePaper, ClinicalTrial
from sitesettings.models import CustomSetting
import feedparser
import gregory.functions as greg
import os
import pytz
import re
import requests
from simple_history.utils import update_change_reason

def update_trials_from_feeds(self):
	sources = Sources.objects.filter(method='rss', source_for='trials')
	for source in sources:
		feed = self.fetch_feed(source.link, source.ignore_ssl)
		for entry in feed['entries']:
			summary = ''
			if hasattr(entry,'summary_detail'):
				summary = entry['summary_detail']['value']
			if hasattr(entry,'summary'):
				summary = entry['summary']
			published = entry.get('published')
			if published:
				published = parse(entry['published'], tzinfos=self.tzinfos).astimezone(pytz.utc)
			link = greg.remove_utm(entry['link'])
			eudract = None
			euct = None
			nct = None
			if "clinicaltrialsregister.eu" in link:
				match = re.search(r'eudract_number\%3A(\d{4}-\d{6}-\d{2})', link)
				if match:
					eudract = match.group(1)
					euct = match.group(1)
			if 'clinicaltrials.gov' in link:
				nct = entry['guid']
			identifiers = {
				"eudract": eudract if eudract is not None else None,
				"euct": euct if euct is not None else None,
				"nct": nct if nct is not None else None
			}
			clinical_trial = ClinicalTrial(title = entry['title'], summary = summary, link = link, published_date = published, identifiers = identifiers,)
			clinical_trial.clean_summary()
			# Find if there's already a trial with the same identifiers
			print(f"trying to find {clinical_trial} in db...")
			query = Q()
			for key, value in identifiers.items():
					if value:
							query |= Q(**{f'identifiers__{key}': value})

			# Use the dynamically constructed query to filter existing trials
			existing_trial = Trials.objects.filter(query).first()
			if not existing_trial:
					print(f"Didn't find trial by identifier, trying title match...")
					existing_trial = Trials.objects.filter(title=entry['title']).first()
					if existing_trial:
							print(f"Found existing trial by title: {existing_trial.pk}")
					else:
							print("No existing trial found by title.")
			else:
					print(f"Found existing trial by identifier: {existing_trial.pk}")          
			if existing_trial:
				# Capture the initial state of the trial
				initial_state = {
						'title': existing_trial.title,
						'summary': existing_trial.summary,
						'link': existing_trial.link,
						'published_date': existing_trial.published_date,
						'identifiers': existing_trial.identifiers,
				}
				try:
						# Attempt to update the existing trial fields including the title
						existing_trial.title = clinical_trial.title
						existing_trial.summary = clinical_trial.summary
						existing_trial.link = clinical_trial.link
						existing_trial.published_date = clinical_trial.published_date
						existing_trial.identifiers = clinical_trial.identifiers
						existing_trial.source = source
						existing_trial.sources.add(source)
						existing_trial.teams.add(source.team)
						existing_trial.subjects.add(source.subject)
						existing_trial.save()
				except IntegrityError:
						# If an IntegrityError occurs, update all except the title
						existing_trial.summary = clinical_trial.summary
						existing_trial.link = clinical_trial.link
						existing_trial.published_date = clinical_trial.published_date
						existing_trial.identifiers = clinical_trial.identifiers
						existing_trial.source = source
						existing_trial.teams.add(source.team)
						existing_trial.subjects.add(source.subject)
						existing_trial.sources.add(source)
						# Explicitly save only the fields that were updated to avoid the IntegrityError
						existing_trial.save(update_fields=['summary', 'link', 'published_date', 'identifiers', 'source'])
						print("Updated trial information, excluding the title due to IntegrityError.")
				if any(initial_state[field] != getattr(existing_trial, field) for field in initial_state):
					change_reason = "Updated from RSS feed."
					update_change_reason(existing_trial, change_reason)
					print(f"Trial {existing_trial.pk} updated.")
				else:
						print(f"No changes detected for Trial {existing_trial.pk}.")
		else:
			# Create a new trial
			try:
				q_objects = Q()
				if clinical_trial.identifiers.get('nct'):
					q_objects |= Q(identifiers__nct=clinical_trial.identifiers.get('nct'))
				if clinical_trial.identifiers.get('eudract'):
					q_objects |= Q(identifiers__eudract=clinical_trial.identifiers.get('eudract'))
				if clinical_trial.identifiers.get('euct'):
					q_objects |= Q(identifiers__euct=clinical_trial.identifiers.get('euct'))
				trial = Trials.objects.get(q_objects)
			except Trials.DoesNotExist:
				# If the trial doesn't exist, create a new one
				try:
					print(f'trying to create {clinical_trial.identifiers}...')
					trial = Trials.objects.create(
						discovery_date=timezone.now(),
						title=clinical_trial.title,
						summary=clinical_trial.summary,
						link=clinical_trial.link,
						published_date=clinical_trial.published_date,
						identifiers=clinical_trial.identifiers,
					)
					trial.teams.add(source.team)
					trial.subjects.add(source.subject)
					trial.sources.add(source)
					print(f'created {trial.trial_id}?')
				except IntegrityError as e:
					print(f"An integrity error occurred: {str(e)}")				
			except MultipleObjectsReturned as e:
				print(f"Multiple entries were found for the same trial identifiers: {str(e)}")
				duplicate_trials = Trials.objects.filter(
					Q(identifiers__nct=clinical_trial.identifiers.get('nct')) |
					Q(identifiers__eudract=clinical_trial.identifiers.get('eudract')) |
					Q(identifiers__euct=clinical_trial.identifiers.get('euct'))
				)
				duplicate_ids = [trial.trial_id for trial in duplicate_trials]
				print("Warning: multiple Trials entries found for identifier. The IDs of the duplicates are: ", duplicate_ids, ". Please resolve manually.")

			else:
			# If the trial exists, update it
				try:
					trial = Trials.objects.get(pk=trial.pk)
					trial.title = clinical_trial.title
					trial.summary = clinical_trial.summary
					trial.link = clinical_trial.link
					trial.published_date = clinical_trial.published_date
					trial.identifiers = clinical_trial.identifiers
					trial.teams.add(source.team)
					trial.subjects.add(source.subject)
					trial.sources.add(source)
					trial.save()						
				except Exception as e:
					print(f"An error occurred: {str(e)}")
					pass
			pass
