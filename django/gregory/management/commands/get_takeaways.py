from bs4 import BeautifulSoup
import html
import time
from django.core.management.base import BaseCommand
from django.db.models.functions import Length
from gregory.models import Articles
from transformers import pipeline


class Command(BaseCommand):
	@staticmethod
	def clean_html(input_text):
			return BeautifulSoup(input_text, 'html.parser').get_text()

	@staticmethod
	def get_summary_max_length(text):
			nr_words = len(text.split())
			max_length = 100
			return min(max_length, int(nr_words / 2))

	@staticmethod
	def summarize_abstract(article_id, abstract, summarizer, min_length=25):
			start = time.time()
			max_length = Command.get_summary_max_length(abstract)
			if max_length > min_length:
					print(f"Summarizing abstract {article_id} with lengths [{min_length}, {max_length}]")
					summary = summarizer(abstract, min_length=min_length, max_length=max_length)
					end = time.time()
					print(f" => Ellapsed time: {end - start} sec.")
					return summary[0]['summary_text']
			return ""

	def handle(self, *args, **options):
			queryset = Articles.objects.annotate(abstract_length=Length('summary')).filter(
					abstract_length__gte=25,
					abstract_length__lte=3000,
					kind='science paper',
					takeaways=None
			)[:1]

			if not queryset:
					print('Nothing to analyse, queryset is empty.')
					exit()

			self.stdout.write("Loading the model")
			summarizer = pipeline("summarization", model='philschmid/bart-large-cnn-samsum')
			self.stdout.write("Summarizer model ready for use")

			for article in queryset:
					abstract = self.clean_html(html.unescape(article.summary)).replace('\n', ' ').replace('\r', ' ')
					takeaways = self.summarize_abstract(article.article_id,abstract, summarizer)
					article.takeaways = takeaways
					article.save()
