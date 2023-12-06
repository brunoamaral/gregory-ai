from .models import Articles, Trials, Sources, Authors
from crossref.restful import Works, Etiquette
from dateutil.parser import parse
from dateutil.tz import gettz
from django_cron import CronJobBase, Schedule
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

tzinfos = {
    "EDT": gettz("America/New_York"),  # Eastern Daylight Time
    "EST": gettz("America/New_York"),  # Eastern Standard Time
    # Add other timezones as needed
}

SITE = CustomSetting.objects.get(site__domain=os.environ.get('DOMAIN_NAME'))
CLIENT_WEBSITE = 'https://' + SITE.site.domain + '/'
my_etiquette = Etiquette(SITE.title, 'v8', CLIENT_WEBSITE, SITE.admin_email)
works = Works(etiquette=my_etiquette)

class FeedReaderTask(CronJobBase):
	RUN_EVERY_MINS = 30
	schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
	code = 'gregory.feedreadertask'    # a unique code

	def do(self):
		###
		# GET ARTICLES
		###
		sources = Sources.objects.filter(method='rss',source_for='science paper')

		for i in sources:
			source_name = i.name
			source_for = i.source_for
			link = i.link
			d = None
			if i.ignore_ssl == False:
				d = feedparser.parse(link)
			else:
				response = requests.get(link, verify=False)
				d = feedparser.parse(response.content)
			for entry in d['entries']:
				title = entry['title']
				summary = ''
				if hasattr(entry,'summary_detail'):
					summary = entry['summary_detail']['value']
				if hasattr(entry,'summary'):
					summary = entry['summary']
				published = entry.get('published')
				if source_name == 'PubMed' and hasattr(entry,'content'):
					summary = entry['content'][0]['value']
				if published:
					published = parse(entry['published'], tzinfos=tzinfos).astimezone(pytz.utc)
				else:
					published = parse(entry['prism_coverdate'], tzinfos=tzinfos).astimezone(pytz.utc)
				link = greg.remove_utm(entry['link'])
				###
				# This is a bad solution but it will have to do for now
				###
				doi = None
				access = None
				journal = None
				publisher = None
				if source_name == 'PubMed':
					if entry['dc_identifier'].startswith('doi:'):
						doi = entry['dc_identifier'].replace('doi:','')
				if source_name == 'FASEB':
					doi = entry['prism_doi']
				if doi != None:
					paper = SciencePaper(doi=doi, abstract=summary, published_date=published, title=title, link=link)
					paper.refresh()
					summary = paper.abstract
					link = paper.link
					access = paper.access
					journal = paper.journal
					publisher = paper.journal
				try:
					science_paper = Articles.objects.create(discovery_date=timezone.now(), title = title, summary = SciencePaper.clean_abstract(abstract=summary), link = link, published_date = published, access = access, publisher = publisher, container_title = journal, source = i, doi = doi, kind = source_for)
					if paper != None:
						# get author information
						for author in paper.authors:
							if 'given' in author and 'family' in author:
								given_name = None
								if 'given' in author:
									given_name = author['given']
								family_name = None
								if 'family' in author:
									family_name = author['family']
								orcid = None
								if 'ORCID' in author:
									orcid = author['ORCID']
								# get or create author
								author_obj = Authors.objects.get_or_create(given_name=given_name,family_name=family_name,ORCID=orcid)
								author_obj = author_obj[0]
								## add to database
								if author_obj.author_id is not None:
									# make relationship
									science_paper.authors.add(author_obj)
						science_paper.save()
						# the articles variable needs to be a queryset list in order to be turned into a pandas dataframe
						greg.predict(articles=Articles.objects.filter(pk=science_paper.article_id))
				except Exception as e:
					# print(f"An error occurred: {str(e)}")
					pass

		###
		# GET TRIALS
		###
		sources = Sources.objects.filter(method='rss',source_for='trials')

		for i in sources:
			source_name = i.name
			source_for = i.source_for
			link = i.link
			d = None
			if i.ignore_ssl == False:
				d = feedparser.parse(link)
			else:
				response = requests.get(link, verify=False)
				d = feedparser.parse(response.content)
			for entry in d['entries']:
				summary = ''
				if hasattr(entry,'summary_detail'):
					summary = entry['summary_detail']['value']
				if hasattr(entry,'summary'):
					summary = entry['summary']
				published = entry.get('published')
				if published:
					published = parse(entry['published'], tzinfos=tzinfos).astimezone(pytz.utc)
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
					"eudract": "EUDRACT" + eudract if eudract is not None else None,
					"euct": "EUCT" + euct if euct is not None else None,
					"nct": nct
				}
				clinical_trial = ClinicalTrial(title = entry['title'], summary = summary, link = link, published_date = published, identifiers = identifiers,)
				clinical_trial.clean_summary()
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
							source=i
						)
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
						trial.source = i
						trial.save()						
					except Exception as e:
						print(f"An error occurred: {str(e)}")
						pass
				pass