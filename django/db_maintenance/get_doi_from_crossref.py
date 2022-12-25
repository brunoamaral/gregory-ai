from crossref.restful import Works, Etiquette
from django_cron import CronJobBase, Schedule
from gregory.models import Articles
import re 
import pytz
import os
from .unpaywall import unpaywall_utils
from sitesettings.models import *
import gregory.functions as greg
from gregory.classes import SciencePaper
from django.utils import timezone



class GetDoiCrossRef(CronJobBase):
	RUN_EVERY_MINS = 60
	schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
	code = 'db_maintenance.get_doi_crossref'    # a unique code

	def do(self):
		# Find DOI
		articles = Articles.objects.filter(kind='science paper',doi__isnull=True,crossref_check__lte=timezone.now(), crossref_check__gt=timezone.now()-timezone.timedelta(days=30)) | Articles.objects.filter(kind='science paper',doi__isnull=True,crossref_check__isnull=True)
		print('Found articles without DOI', articles.count())
		for article in articles:
			doi = greg.get_doi(article.title)
			article.crossref_check = timezone.now()
			article.save()
			if doi is not None:
				article.doi = doi
				article.save()

		# Get access info
		articles = Articles.objects.filter(doi__isnull=False,access__isnull=True,kind='science paper',crossref_check__lte=timezone.now(), crossref_check__gt=timezone.now()-timezone.timedelta(days=30)) | Articles.objects.filter(doi__isnull=False,access__isnull=True,kind='science paper', crossref_check__isnull = True)
		print('Found articles with no access information,',articles.count())
		for article in articles:
			paper = SciencePaper(doi=article.doi)
			paper.refresh()
			article.crossref_check = timezone.now()
			article.access = paper.access
			article.save()

		# Get publisher and journal
		print('Filling in the publisher field...')
		articles = Articles.objects.filter(publisher__isnull=True,doi__isnull=False,crossref_check__lte=timezone.now(), crossref_check__gt=timezone.now()-timezone.timedelta(days=30)) | Articles.objects.filter(publisher__isnull=True,doi__isnull=False,crossref_check__isnull=True)
		print('Found articles that need publisher information',articles.count())
		for article in articles:
			paper = SciencePaper(doi=article.doi)
			paper.refresh()
			article.publisher = paper.publisher
			article.container_title = paper.journal
			article.crossref_check = timezone.now()
			article.save()

		# Get published date
		articles = Articles.objects.filter(published_date__isnull=True,doi__isnull=False,crossref_check__lte=timezone.now(), crossref_check__gt=timezone.now()-timezone.timedelta(days=30)) | Articles.objects.filter(published_date__isnull=True,doi__isnull=False,crossref_check__lte=timezone.now(), crossref_check__isnull=True)
		print('Found articles that need publish date information',articles.count())
		for article in articles:
			paper = SciencePaper(doi=article.doi)
			paper.refresh()
			article.crossref_check = timezone.now()
			article.published_date = paper.published_date
			article.save()

		# Get abstracts
		articles = Articles.objects.filter(summary=None,authors__isnull=True,doi__isnull=False,crossref_check__lte=timezone.now(), crossref_check__gt=timezone.now()-timezone.timedelta(days=30)) | Articles.objects.filter(summary=None,authors__isnull=True,doi__isnull=False,crossref_check__isnull=True) | Articles.objects.filter(doi__isnull=False,summary='not available', crossref_check__isnull=True)
		print('found articles that need abstract',articles.count())
		for article in articles:
			science_paper = SciencePaper(doi=article.doi)
			paper.refresh()
			article.crossref_check = timezone.now()
			article.save()
			if science_paper.abstract != None:
				article.summary = science_paper.abstract
				article.save()
