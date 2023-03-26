from bs4 import BeautifulSoup
import html
import pandas as pd
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
	def summarize_abstract(row, summarizer, min_length=25):
			start = time.time()
			max_length = Command.get_summary_max_length(row['abstract'])
			if max_length > min_length:
					print(f"Summarizing abstract #{row['article_id']} with lengths [{min_length}, {max_length}]")
					summary = summarizer(row['abstract'], min_length=min_length, max_length=max_length)
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

			dataset = pd.DataFrame(list(queryset.values("article_id", "summary")))

			if dataset.empty:
					print('Nothing to analyse, dataset is empty.')
					exit()

			dataset = dataset.rename(columns={"summary": "abstract"})
			dataset["abstract"] = dataset["abstract"].apply(html.unescape).apply(self.clean_html)
			dataset = dataset.replace(r'\n|\r', ' ', regex=True)

			self.stdout.write("Loading the model")
			summarizer = pipeline("summarization")
			self.stdout.write("Summarizer model ready for use")

			dataset['get_takeaways'] = dataset.apply(lambda row: self.summarize_abstract(row, summarizer), axis=1)

			for _, row in dataset.iterrows():
					article = Articles.objects.get(pk=row['article_id'])
					article.takeaways = row['get_takeaways']
					article.save()
