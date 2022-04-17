from django.conf import settings

from django_cron import CronJobBase, Schedule

from gregory.models import Articles
from django.db.models import Q
import spacy 

nlp = spacy.load('en_core_web_sm')
class NounPhrases(CronJobBase):
	RUN_EVERY_MINS = 20
	schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
	code = 'gregory.noun_phrases'    # a unique code

	def do(self):
		articles = Articles.objects.filter(noun_phrases__isnull=True)[:10]
		# select title,article_id from articles where noun_phrases IS NULL limit 1;
		if len(articles) != 0:
			for article in articles:
				doc=nlp(article.title )
				# Analyze syntax
				noun_phrases = [chunk.text for chunk in doc.noun_chunks]
				article.noun_phrases = noun_phrases
				print(article.article_id)
				article.save()
	pass

