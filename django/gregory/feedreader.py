import feedparser
from dateutil.parser import parse
from datetime import datetime
from dotenv import load_dotenv
from .models import Articles,Trials,Sources
from django_cron import CronJobBase, Schedule
import requests


class FeedReaderTask(CronJobBase):
	RUN_EVERY_MINS = 1 #30
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
			d = None
			if i.ignore_ssl == False:
				d = feedparser.parse(link)
			else:
				response = requests.get(link, verify=False)
				d = feedparser.parse(response.content)
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

		for i in sources:
			source_name = i.name
			source_for = i.source_for
			link = i.link
			d = None
			if i.ignore_ssl == False:
				d = feedparser.parse(link)
			else:
				response = requests.get(link, verify=False)
				d = feedparser.parse(response.content)
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
					trial = Trials.objects.create( discovery_date=datetime.now(), title = entry['title'], summary = summary, link = entry['link'], published_date = published)
				except:
					pass
