from django.core.management.base import BaseCommand, CommandError
import pandas as pd
from gregory.utils.text_utils import cleanText
from gregory.utils.text_utils import cleanHTML
import html
from gregory.models import Articles


class Command(BaseCommand):
	def handle(self, *args, **options):
		# Read the JSON data into a pandas dataframe
		queryset = Articles.objects.filter(title__isnull=False,summary__isnull=False).values()
		dataset = pd.DataFrame(list(queryset))
		# if queryset is None:
		# 	print('empty queryset')
		# 	return
		# Give some info on the dataset
		# dataset.info()

		# List of columns that we actually need from the dataset
		valid_columns = ["title", "summary", "relevant"]

		# Strip the dataset to only those columns
		dataset = dataset[valid_columns]

		# Clean the title column with the cleanText utility
		dataset["title"] = dataset["title"].apply(cleanText)

		# Some columns in the summary column seem to be encoded in HTML, let's decode it
		dataset["summary"] = dataset["summary"].apply(html.unescape)

		# Now let's remove all those HTML tags
		dataset["summary"] = dataset["summary"].apply(cleanHTML)

		# Now let's clean the resulting text in the summary column
		dataset["summary"] = dataset["summary"].apply(cleanText)

		# Let's join the two text columns into a single 'terms' column
		dataset["terms"] = dataset["title"].astype(str) + " " + dataset["summary"].astype(str)

		# We no longer need the two text columns, let's remove them
		dataset = dataset[["terms", "relevant"]]

		# There are several records in the "relevant" column as NaN. Let's convert them to zeros
		dataset.loc[:, "relevant"] = dataset["relevant"].fillna(value = 0)

		SOURCE_DATA_CSV = "/code/gregory/data/source.csv"
		dataset.to_csv(SOURCE_DATA_CSV, index=False)
		print(dataset)
	pass