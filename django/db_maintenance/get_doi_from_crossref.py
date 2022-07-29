from crossref.restful import Works, Etiquette
from django_cron import CronJobBase, Schedule
from gregory.models import Articles
import re 

class GetDoiCrossRef(CronJobBase):
	RUN_EVERY_MINS = 600 # every 10h
	schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
	code = 'db_maintenance.get_doi_crossref'    # a unique code

	def do(self):
		my_etiquette = Etiquette('Gregory MS', 'v8', 'https://gregory-ms.com', 'bruno@gregory-ms.com')
		works = Works(etiquette=my_etiquette)
		articles = Articles.objects.filter(doi=None)
		for article in articles:
			print(article.article_id)
			if article.article_id != 237:
				i = 0
				work = works.query(bibliographic=article.title).sort('relevance')
				for w in work:
					crossref_title = ''
					article_title = re.sub(r'[^A-Za-z0-9 ]+', '', article.title)
					article_title = re.sub(r' ','',article_title ).lower()
					crossref_title = re.sub(r'[^A-Za-z0-9 ]+', '', w['title'][0])
					crossref_title = re.sub(r' ','',crossref_title).lower()
					print(crossref_title)
					print(article_title)
					if crossref_title == article_title:
						print(article_title)
						article.doi = w['DOI']
						article.save()
					else:
						i = 1
					if i == 1:
						break