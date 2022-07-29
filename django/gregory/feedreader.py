import feedparser
import psycopg2
from dotenv import load_dotenv
import os
import ssl
from models import Articles,Sources
from django_cron import CronJobBase, Schedule

load_dotenv()


class FeedReaderTask(CronJobBase):
	RUN_EVERY_MINS = 5
	schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
	code = 'gregory.feedreadertask'    # a unique code

	def do(self):
		###
		# GET ARTICLES
		###
		sources = Sources.objects.filter(method='rss',source_for='science paper')

		for i in sources:
			source_id = i.source_id
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
					published = entry['published']
				else:
					published = entry['prism_coverdate']
				###
				# This is a bad solution but it will have to do for now
				###
				doi = None 
				if source_name == 'PubMed':
					doi = entry['dc_identifier'].replace('doi:','')
				if source_name == 'FASEB':
					doi = entry['prism_doi']
				science_paper = Articles.objects.create(
					title = entry['title'], summary = summary, link = entry['link'], published_date = published, source_link = link, source = source_id, doi = doi, kind = source_for
				)


		###
		# GET TRIALS
		###

		# INSERT INTO trials (discovery_date,title,summary,link,published_date,source,relevant)
		# VALUES (current_timestamp,'{{article.title}}','{{article.description}}','{{{topic}}}','{{article.pubdate}}','{{article.source}}',NULL)
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
					published = entry['published']
				trial = Trials.objects.create(
					title = entry['title'], summary = summary, link = entry['link'], published_date = published, link = link, source = source_id
				) 

		pass