from django.core.management.base import BaseCommand
from gregory.models import Articles, Trials, Sources, Authors
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
class Command(BaseCommand):
    help = 'Fetches and updates articles and trials from RSS feeds.'

    def handle(self, *args, **options):
        self.SITE = CustomSetting.objects.get(site__domain=os.environ.get('DOMAIN_NAME'))
        self.CLIENT_WEBSITE = 'https://' + self.SITE.site.domain + '/'
        self.my_etiquette = Etiquette(self.SITE.title, 'v8', self.CLIENT_WEBSITE, self.SITE.admin_email)
        self.works = Works(etiquette=self.my_etiquette)
        self.tzinfos = {"EDT": gettz("America/New_York"), "EST": gettz("America/New_York")}
        
        self.update_articles_from_feeds()
        self.update_trials_from_feeds()

    def update_articles_from_feeds(self):
        sources = Sources.objects.filter(method='rss', source_for='science paper')
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
              published = parse(entry['published'], tzinfos=self.tzinfos).astimezone(pytz.utc)
            else:
              published = parse(entry['prism_coverdate'], tzinfos=self.tzinfos).astimezone(pytz.utc)
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
    def update_trials_from_feeds(self):
        sources = Sources.objects.filter(method='rss', source_for='trials')
        for source in sources:
            link = source.link
            d = None
            if not source.ignore_ssl:
                d = feedparser.parse(link)
            else:
                response = requests.get(link, verify=False)  # Be cautious with verify=False
                d = feedparser.parse(response.content)

            for entry in d['entries']:
                summary = entry.get('summary_detail', {}).get('value', '') or entry.get('summary', '')
                published = entry.get('published')
                if published:
                    published = parse(entry['published'], tzinfos=self.tzinfos).astimezone(pytz.utc)
                link = greg.remove_utm(entry['link'])
                eudract, euct, nct = None, None, None

                if "clinicaltrialsregister.eu" in link:
                    match = re.search(r'eudract_number\%3A(\d{4}-\d{6}-\d{2})', link)
                    if match:
                        eudract = match.group(1)
                        euct = match.group(1)
                if 'clinicaltrials.gov' in link:
                    nct = entry.get('guid')
                
                identifiers = {
                    "eudract": eudract,
                    "euct": euct,
                    "nct": nct
                }

                try:
                    # Check if a trial with the same title already exists
                    existing_trial = Trials.objects.get(title=entry['title'])
                    print(f"Trial with title '{existing_trial.title}' already exists. Skipping.")
                except Trials.DoesNotExist:
                    # No existing trial with the same title, proceed to create
                    try:
                        trial = Trials.objects.create(
                            discovery_date=timezone.now(),
                            title=entry['title'],
                            summary=summary,
                            link=link,
                            published_date=published,
                            identifiers=identifiers,
                            source=source
                        )
                        print(f"Created trial with ID {trial.trial_id}.")
                    except IntegrityError as e:
                        print(f"An integrity error occurred while creating trial '{entry['title']}': {e}")
                except Trials.MultipleObjectsReturned:
                    print(f"Multiple trials found with title '{entry['title']}'. Please review.")
