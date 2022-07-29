import feedparser
from dateutil.parser import parse
from datetime import datetime
from dotenv import load_dotenv
import ssl
from .models import Articles,Trials,Sources
from django_cron import CronJobBase, Schedule

load_dotenv()


class FeedReaderTask(CronJobBase):
	RUN_EVERY_MINS = 30
	schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
	code = 'gregory.feedreadertask'    # a unique code

	def do(self):
		###
		# GET ARTICLES
		###
		sources = Sources.objects.filter(method='rss',source_for='science paper')

		for i in sources:
			source_name = i.name
			source_for = i.source_for
			link = i.link
			d = feedparser.parse(link)
			for entry in d['entries']:
				summary = ''
				if hasattr(entry,'summary_detail'):
					summary = entry['summary_detail']['value']
				if hasattr(entry,'summary'):
					summary = entry['summary']
				published = entry.get('published')
				if published:
					published = parse(entry['published'])
				else:
					published = parse(entry['prism_coverdate'])
				###
				# This is a bad solution but it will have to do for now
				###
				doi = None 
				if source_name == 'PubMed':
					doi = entry['dc_identifier'].replace('doi:','')
				if source_name == 'FASEB':
					doi = entry['prism_doi']
				try:
					science_paper = Articles.objects.create(
					discovery_date=datetime.now(), title = entry['title'], summary = summary, link = entry['link'], published_date = published, source = i, doi = doi, kind = source_for )
				except:
					pass




		###
		# GET TRIALS
		###

		sources = Sources.objects.filter(method='rss',source_for='trials')

		# This disables the SSL verification. The only reason why we are doing this is because of issue #55 <https://github.com/brunoamaral/gregory/issues/55> 
		if hasattr(ssl, '_create_unverified_context'):
			ssl._create_default_https_context = ssl._create_unverified_context

		for i in sources:
			source_id = i.source_id
			link = i.link
			d = feedparser.parse(link)
			for entry in d['entries']:
				summary = ''
				if hasattr(entry,'summary_detail'):
					summary = entry['summary_detail']['value']
				if hasattr(entry,'summary'):
					summary = entry['summary']
				published = entry.get('published')
				if published:
					published = parse(entry['published'])
				try:
						trial = Trials.objects.create( discovery_date=datetime.now(), title = entry['title'], summary = summary, link = entry['link'], published_date = published, source = i) 
				except:
						pass

		pass