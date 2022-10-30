from crossref.restful import Works, Etiquette
from django_cron import CronJobBase, Schedule
from gregory.models import Articles
import re 
import pytz
from datetime import datetime
import os
from .unpaywall import unpaywall_utils
from sitesettings.models import *
import gregory.functions as greg

class GetDoiCrossRef(CronJobBase):
	RUN_EVERY_MINS = 60 
	schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
	code = 'db_maintenance.get_doi_crossref'    # a unique code

	def do(self):
		# Find DOI
		articles = Articles.objects.filter(doi=None)
		for article in articles:
			doi = greg.get_doi(article.title)
			if doi is not None:
				article.doi = doi
				article.save()

		# Get access info
		articles = Articles.objects.filter(doi__isnull=False,access__isnull=True,kind='science paper')
		print('found articles with no access information,',articles.count())
		for article in articles:
			article.access = greg.get_access_info(article.doi)
			article.save()

		# Get publisher and journal
		print('filling in the publisher field...')
		articles = Articles.objects.filter(publisher__isnull=True,doi__isnull=False)
		print('found articles that need publisher information',articles.count())
		for article in articles:
			publisher_journal = greg.get_publisher_and_journal(article.doi)
			article.publisher = publisher_journal[0]
			article.container_title = publisher_journal[1]
			article.save()

		# Get published date
		articles = Articles.objects.filter(published_date=None,doi__isnull=False)
		print('found articles that need publish date information',articles.count())
		timezone = pytz.timezone('UTC')
		for article in articles:
			article.published_date = greg.get_published_date(article.doi)
			article.save()

		# Get abstracts
		articles = Articles.objects.filter(summary=None)
		print('found articles that need abstract',articles.count())
		for article in articles:
			article.summary = greg.get_abstract(article.doi)
			article.save()
			
