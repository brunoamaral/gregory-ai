from bs4 import BeautifulSoup
from transformers import pipeline
import csv
import html
import pandas as pd
import time
from models import Articles

# Read the Django data into a pandas dataframe
dataset = pd.DataFrame(list(Articles.objects.filter(takeaways=None,summary__gt=25,kind="science paper").values("article_id", "summary",)))

# List of columns that we actually need from the dataset. 'summary' represents the article's abstract.
valid_columns = ["article_id", "summary"]

# Strip the dataset to only those columns
dataset = dataset[valid_columns]

# Let's also change the misleading 'summary' name to 'abstract'
dataset = dataset.rename(columns = { "summary" : "abstract" })

# Take only the rows in which the 'abstract' column is not null
dataset = dataset[dataset['abstract'].notna()]

dataset.sample(n=5)

# Util function to clean HTML
def cleanHTML(input):
		return BeautifulSoup(input, 'html.parser').get_text()

# Some columns in the 'abstract' column seem to be encoded in HTML, let's decode it
dataset["abstract"] = dataset["abstract"].apply(html.unescape)

# Now let's remove all those HTML tags
dataset["abstract"] = dataset["abstract"].apply(cleanHTML)

# Let's also remove newlines
dataset = dataset.replace(r'\n|\r',' ', regex=True)


print("Loading the model")

# Initialize the HuggingFace summarization pipeline
summarizer = pipeline("summarization")

print("Summarizer model ready for use")


# Minimum length of the summary (in words/tokens?)
MIN_LENGTH = 25
MAX_LENGTH = 100

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

		return summary[0]['summary_text']
	
	return ""

test_sample = dataset.sample(n=100)
test_sample['summary'] = test_sample.apply(lambda row: summarizeAbstract(row), axis=1)

test_sample.info()


test_sample.to_csv('data/results.csv', index=False, quoting=csv.QUOTE_NONNUMERIC)

