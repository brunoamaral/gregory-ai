from crossref.restful import Works, Etiquette
from django_cron import CronJobBase, Schedule
from gregory.models import Articles
import re 

from .unpaywall import unpaywall_utils

class GetDoiCrossRef(CronJobBase):
	RUN_EVERY_MINS = 300 # every 5h
	schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
	code = 'db_maintenance.get_doi_crossref'    # a unique code

	def do(self):
		my_etiquette = Etiquette('Gregory MS', 'v8', 'https://gregory-ms.com', 'bruno@gregory-ms.com')
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
						
		CLIENT_EMAIL = "bruno@gregory-ms.com"
		articles = Articles.objects.filter(doi__isnull=False,access='unknown',kind='science paper')
		print('found articles with no access information,',articles.count())
		for article in articles:
			if bool(article.doi):
				if unpaywall_utils.checkIfDOIIsOpenAccess(article.doi, CLIENT_EMAIL):
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