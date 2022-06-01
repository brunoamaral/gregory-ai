from crossref.restful import Works, Etiquette
from django_cron import CronJobBase, Schedule
from gregory.models import Articles

class GetDoiCrossRef(CronJobBase):
	RUN_EVERY_MINS = 600 # every 10h
	schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
	code = 'db_maintenance.get_doi_crossref'    # a unique code

	def do(self):
		my_etiquette = Etiquette('Gregory MS', 'v8', 'https://gregory-ms.com', 'bruno@gregory-ms.com')
		works = Works(etiquette=my_etiquette)
		articles = Articles.objects.filter(doi=None)

		for article in articles:
			i = 0
			work = works.query(bibliographic=article.title).sort('relevance')
			for w in work:
				title = ''
				if hasattr(w,'title'):
					title = w['title'][0]
				if title.lower() == article.title.lower():
					article.doi = w['DOI']
					article.save()
				else:
					i = 1
				if i == 1:
					break

