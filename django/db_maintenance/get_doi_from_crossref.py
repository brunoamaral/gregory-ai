from crossref.restful import Works, Etiquette
from django_cron import CronJobBase, Schedule
from gregory.models import Articles
import re 
import pytz
from datetime import datetime
import os
from .unpaywall import unpaywall_utils
from sitesettings.models import *

class GetDoiCrossRef(CronJobBase):
	RUN_EVERY_MINS = 60 
	schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
	code = 'db_maintenance.get_doi_crossref'    # a unique code

	def do(self):
		SITE = CustomSetting.objects.get(site__domain=os.environ.get('DOMAIN_NAME'))
		CLIENT_WEBSITE = 'https://' + SITE.site.domain + '/'
		my_etiquette = Etiquette(SITE.title, 'v8', CLIENT_WEBSITE, SITE.admin_email)
		works = Works(etiquette=my_etiquette)
		articles = Articles.objects.filter(doi=None)
		for article in articles:
			if article.title != '':
				i = 0
				work = works.query(bibliographic=article.title).sort('relevance')
				for w in work:
					if 'title' in w:
						crossref_title = ''
						article_title = re.sub(r'[^A-Za-z0-9 ]+', '', article.title)
						article_title = re.sub(r' ','',article_title ).lower()
						crossref_title = re.sub(r'[^A-Za-z0-9 ]+', '', w['title'][0])
						crossref_title = re.sub(r' ','',crossref_title).lower()
						if crossref_title == article_title:
							article.doi = w['DOI']
							article.save()
						i += 1
						if i == 5:
							break
						
		articles = Articles.objects.filter(doi__isnull=False,access__isnull=True,kind='science paper')
		print('found articles with no access information,',articles.count())
		for article in articles:
			if bool(article.doi):
				if unpaywall_utils.checkIfDOIIsOpenAccess(article.doi, SITE.admin_email):
					article.access = 'open'
					# if article.access == 'open':
					# 	pdf_url = unpaywall_utils.getOpenAccessURLForDOI(article.doi, CLIENT_EMAIL)
				else:
					article.access = 'restricted'
				article.save()

		print('filling in the publisher field...')
		articles = Articles.objects.filter(publisher__isnull=True,doi__isnull=False)
		print('found articles that need publisher information',articles.count())
		for article in articles:
			if bool(article.doi):
				work = works.doi(article.doi)
				if work:
					print(work['publisher'])
					article.publisher = work['publisher']
					article.container_title = work['container-title'][0]
					article.save()
				else:
					print(article.article_id)
		articles = Articles.objects.filter(published_date=None,doi__isnull=False)
		print('found articles that need publish date information',articles.count())
		timezone = pytz.timezone('UTC')
		for article in articles:
			w = works.doi(article.doi)
			if w != None and 'issued' in w:
				issued = w['issued']['date-parts'][0]
				year,month,day = None,1,1
				print(issued)
				try:
					year = issued[0]
				except:
					pass
				try:
					month=issued[1]
				except:
					pass
				try:
					day=issued[2]
				except:
					pass
				try:
					published_date = datetime( year=year, month=month, day=day, tzinfo=timezone)
					article.published_date = published_date
					article.save()
				except:
						pass

			if article.summary == None:
				try:
					article.summary = w['abstract']
				except:
					pass
			article.save()
		articles = Articles.objects.filter(summary=None)
		print('found articles that need abstract',articles.count())
		for article in articles:
			if hasattr(article,'doi') and article.doi != None:
				w = works.doi(article.doi)
				try:
						article.summary = w['abstract']
						article.save()
				except:
						pass
