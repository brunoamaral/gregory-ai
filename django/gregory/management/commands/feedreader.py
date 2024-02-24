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

class Command(BaseCommand):
    help = 'Fetches and updates articles and trials from RSS feeds.'

    def handle(self, *args, **options):
        self.setup()
        self.update_articles_from_feeds()
        # self.update_trials_from_feeds()

    def setup(self):
        self.SITE = CustomSetting.objects.get(site__domain=os.environ.get('DOMAIN_NAME'))
        self.CLIENT_WEBSITE = f'https://{self.SITE.site.domain}/'
        my_etiquette = Etiquette(self.SITE.title, 'v8', self.CLIENT_WEBSITE, self.SITE.admin_email)
        self.works = Works(etiquette=my_etiquette)
        self.tzinfos = {"EDT": gettz("America/New_York"), "EST": gettz("America/New_York")}

    def fetch_feed(self, link, ignore_ssl):
      if not ignore_ssl:
          return feedparser.parse(link)
      else:
          response = requests.get(link, verify=False)
          return feedparser.parse(response.content)

    def handle_database_error(self, action, error):
        """Generic error handler for database operations."""
        # Log the error or print it. Replace print with logging in production code.
        print(f"An error occurred during {action}: {str(error)}")

    def update_articles_from_feeds(self):
      sources = Sources.objects.filter(method='rss', source_for='science paper')
      for source in sources:
          feed = self.fetch_feed(source.link, source.ignore_ssl)
          for entry in feed['entries']:
              title = entry['title']
              summary = entry.get('summary', '')
              if hasattr(entry, 'summary_detail'):
                  summary = entry['summary_detail']['value']
              published = entry.get('published')
              if 'pubmed' in source.link and hasattr(entry, 'content'):
                  summary = entry['content'][0]['value']
              published_date = parse(entry.get('published') or entry.get('prism_coverdate'), tzinfos=self.tzinfos).astimezone(pytz.utc)
              link = greg.remove_utm(entry['link'])
              doi = None
              if 'pubmed' in source.link and entry.get('dc_identifier', '').startswith('doi:'):
                  doi = entry['dc_identifier'].replace('doi:', '')
              elif 'faseb' in source.link:
                  doi = entry.get('prism_doi', '')

              if doi:
                  crossref_paper = SciencePaper(doi=doi)
                  crossref_paper.refresh()
                  # Check if the article exists in the database
                  science_paper, created = Articles.objects.get_or_create(doi=doi, defaults={
                      'title': crossref_paper.title,
                      'summary': crossref_paper.abstract,
                      'link': link,
                      'published_date': published_date,
                      'source': source,
                      'container_title': crossref_paper.journal,
                      'publisher': crossref_paper.publisher,
                      'access': crossref_paper.access
                      # other fields like access, journal, publisher can be added here as defaults
                  })

                  if not created:                      
                      if any([science_paper.title != title, science_paper.summary != SciencePaper.clean_abstract(abstract=summary),
                              science_paper.link != link, science_paper.published_date != published_date]):
                          science_paper.title = title
                          science_paper.summary = SciencePaper.clean_abstract(abstract=summary)
                          science_paper.link = link
                          science_paper.published_date = published_date
                          science_paper.save()
                  # Process author information
                  if crossref_paper is not None:  # Assuming `paper` contains the article's metadata including author information
                    for author_info in crossref_paper.authors:
                      given_name = author_info.get('given')
                      family_name = author_info.get('family')
                      orcid = author_info.get('ORCID', None)
                      try:
                        if orcid:  # If ORCID is present, use it as the primary key for author lookup/creation
                          author_obj, author_created = Authors.objects.get_or_create(
                              ORCID=orcid,
                              defaults={
                                  'given_name': given_name,
                                  'family_name': family_name
                                  }
                              )
                        else:  # If no ORCID is provided, fallback to using given_name and family_name for lookup/creation
                          author_obj, author_created = Authors.objects.get_or_create(
                              given_name=given_name,
                              family_name=family_name,
                              defaults={'ORCID': orcid}  # orcid will be an empty string if not provided, which is fine
                          )
                      except MultipleObjectsReturned:
                        # Handle the case where multiple authors are returned
                        authors = Authors.objects.filter(given_name=given_name, family_name=family_name)
                        print(f"Multiple authors found for {given_name} {family_name}:")
                        for author in authors:
                            print(f"Author ID: {author.author_id}, ORCID: {author.ORCID}")
                        # Use the first author with an ORCID, if available
                        author_obj = next((author for author in authors if author.ORCID), authors.first())


                        # Link the author to the article if not already linked
                      if not science_paper.authors.filter(pk=author_obj.pk).exists():
                        science_paper.authors.add(author_obj)
              else:
                print('no DOI, trying to create article')
                science_paper, created = Articles.objects.get_or_create(title=title, defaults={
                    'title': title,
                    'summary': abstract,
                    'link': link,
                    'published_date': published_date,
                    'source': source
                    # other fields like access, journal, publisher can be added here as defaults
                })
                if not created:                      
                  if any([science_paper.title != title, science_paper.summary != SciencePaper.clean_abstract(abstract=summary),
                        science_paper.link != link, science_paper.published_date != published_date]):
                    science_paper.title = title
                    science_paper.summary = SciencePaper.clean_abstract(abstract=summary)
                    science_paper.link = link
                    science_paper.published_date = published_date
                    science_paper.save()

                  # last step, run ML relevance predictor
              should_run_prediction = (
                science_paper.ml_prediction_gnb is None or
                science_paper.ml_prediction_lr is None
                # science_paper.ml_prediction_lsvc is None
              )
              if should_run_prediction:
                print(science_paper)
                greg.predict(articles=Articles.objects.filter(pk=science_paper.article_id))


    ###
    # GET TRIALS
    ###
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

          # Get the identifiers
          nct = clinical_trial.identifiers.get('nct')
          euct = clinical_trial.identifiers.get('euct')
          eudract = clinical_trial.identifiers.get('eudract')
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
            # Update the existing trial fields
            existing_trial.title = clinical_trial.title
            existing_trial.summary = clinical_trial.summary
            existing_trial.link = clinical_trial.link
            existing_trial.published_date = clinical_trial.published_date
            existing_trial.identifiers = clinical_trial.identifiers
            existing_trial.source = source
            existing_trial.save()
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
                source=source
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
              trial.source = source
              trial.save()						
            except Exception as e:
              print(f"An error occurred: {str(e)}")
              pass
          pass
