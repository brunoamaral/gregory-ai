from bs4 import BeautifulSoup
from transformers import pipeline
import html
import pandas as pd
import time
from gregory.models import Articles
from django.core.management.base import BaseCommand, CommandError
from django.db.models.functions import Length

def remove_last_sentence(text: str) -> str:
		sentences = text.split('. ')
		if len(sentences) == 1:
				return text
		else:
				return '. '.join(sentences[:-1])

class Command(BaseCommand):
	def handle(self, *args, **options):
		# Read the Django data into a pandas dataframe
		dataset = pd.DataFrame(list(Articles.objects.annotate(abstract_length=Length('summary')).filter(abstract_length__gte=25).filter(abstract_length__lte=3000).filter(kind='science paper').filter(takeaways=None)[:100].values("article_id", "summary",)))
		print(dataset.count())
		dataset = dataset[:10]
		if dataset.empty == True:
			print('Nothing to analyse, dataset is empty.')
			exit() 
		# List of columns that we actually need from the dataset. 'summary' represents the article's abstract.
		valid_columns = ["article_id", "summary"]

		# Strip the dataset to only those columns
		dataset = dataset[valid_columns]

		# Let's also change the misleading 'summary' name to 'abstract'
		dataset = dataset.rename(columns = { "summary" : "abstract" })

		# Take only the rows in which the 'abstract' column is not null
		dataset = dataset[dataset['abstract'].notna()]

		# Util function to clean HTML
		def cleanHTML(input):
				return BeautifulSoup(input, 'html.parser').get_text()

		# Some columns in the 'abstract' column seem to be encoded in HTML, let's decode it
		dataset["abstract"] = dataset["abstract"].apply(html.unescape)

		# Now let's remove all those HTML tags
		dataset["abstract"] = dataset["abstract"].apply(cleanHTML)

		# Let's also remove newlines
		dataset = dataset.replace(r'\n|\r',' ', regex=True)


		self.stdout.write("Loading the model")

		# Initialize the HuggingFace summarization pipeline
		summarizer = pipeline("summarization")

		self.stdout.write("Summarizer model ready for use")

		# Minimum length of the summary (in words/tokens?)
		MIN_LENGTH = 25
		MAX_LENGTH = 150

		# Calculates the max length of the summary (in words) considering the size of the input text
		def getSummaryMaxLengthForText(text):
			nr_words = len(text.split())
			if nr_words / 2 > MAX_LENGTH:
				return MAX_LENGTH
			return int(nr_words / 2)

		# Util function that summarizes the article's abstract
		def summarizeAbstract(row):
			start = time.time()
			max_length = getSummaryMaxLengthForText(row['abstract'])
			if max_length > MIN_LENGTH:
				print("Summarizing abstract #", str(row['article_id']), "with lengths [", str(MIN_LENGTH), ",", str(max_length), "]")
				summary = summarizer(row['abstract'], min_length=MIN_LENGTH, max_length=max_length)
				end = time.time()
				print(" => Ellapsed time: ", end - start, "sec.")
				return remove_last_sentence(summary[0]['summary_text'])
			return ""

		dataset['get_takeaways'] = dataset.apply(lambda row: summarizeAbstract(row), axis=1)

		for index, row in dataset.iterrows():
			article = Articles.objects.get(pk=row['article_id'])
			article.takeaways = row['get_takeaways']
			article.save()

