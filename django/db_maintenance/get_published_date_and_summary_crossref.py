from crossref.restful import Works, Etiquette
from django_cron import CronJobBase, Schedule
from gregory.models import Articles,Trials
from django.db.models import Q
from datetime import datetime
import pytz

class GetDateSummaryCrossRef(CronJobBase):
	RUN_EVERY_MINS = 90 # every 1h30m
	schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
	code = 'db_maintenance.get_date_abstract_from_crossref'    # a unique code

	def do(self):
		my_etiquette = Etiquette('Gregory MS', 'v8', 'https://gregory-ms.com', 'bruno@gregory-ms.com')
		works = Works(etiquette=my_etiquette)
		articles = Articles.objects.filter(published_date=None)
		timezone = pytz.timezone('UTC')
		for article in articles:
			if hasattr(article,'doi') and article.doi != None:
				w = works.doi(article.doi)
				issued = w['issued']['date-parts'][0]
				try:
					published_date = datetime( year=issued[0], month=issued[1], day=issued[2], tzinfo=timezone)
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
		for article in articles:
			if hasattr(article,'doi') and article.doi != None:
				w = works.doi(article.doi)
				try:
						article.summary = w['abstract']
						article.save()
				except:
						pass
