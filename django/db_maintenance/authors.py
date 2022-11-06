from crossref.restful import Works, Etiquette
from dotenv import load_dotenv
from django.conf import settings
from django_cron import CronJobBase, Schedule
from django.template.loader import get_template
from gregory.models import Articles,Authors
from django.db.models import Q
import os
from sitesettings.models import *
from django.utils import timezone
import pytz

class GetAuthors(CronJobBase):
	RUN_EVERY_MINS = 150
	schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
	code = 'db_maintenance.get_authors'    # a unique code

	def do(self):
		load_dotenv()
		SITE = CustomSetting.objects.get(site__domain=os.environ.get('DOMAIN_NAME'))
		CLIENT_WEBSITE = 'https://' + SITE.site.domain + '/'
		my_etiquette = Etiquette(SITE.title, 'v8', CLIENT_WEBSITE, SITE.admin_email)
		works = Works(etiquette=my_etiquette)
		articles = Articles.objects.filter(authors__isnull=True,doi__isnull=False,crossref_check__lte=timezone.now(), crossref_check__gt=timezone.now()-timezone.timedelta(days=30)) | Articles.objects.filter(authors__isnull=True,doi__isnull=False,crossref_check__isnull=True)
		for article in articles:
			w = works.doi(article.doi)
			if w is not None and 'author' in w and w['author'] is not None:
				authors = w['author']
				for author in authors:
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
							article.authors.add(author_obj)
			article.crossref_check = timezone.now()
			article.save()
