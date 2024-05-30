from bs4 import BeautifulSoup
from datetime import datetime
from joblib import dump
from nltk.corpus import stopwords as stopwords
from sklearn.base import TransformerMixin
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
from sklearn.multiclass import OneVsRestClassifier
from sklearn.naive_bayes import GaussianNB, MultinomialNB
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC
import concurrent.futures
import html
import json
import math
import nltk
import pandas as pd
import re
import requests
import argparse
from tqdm import tqdm  # Progress bar library

# Script to be run locally. It will download the db from the API or load from source.csv

nltk.download('stopwords')
STOPWORDS = set(stopwords.words('english'))
get_api_url = "https://api.gregory-ms.com/articles/?format=json"
api_key = "your_api_key" # not need for reading permissions
REPLACE_BY_SPACE_RE = re.compile('[/(){}\[\]\|@,;]')
BAD_SYMBOLS_RE = re.compile('[^0-9a-z #+_]')

# check for arguments
parser = argparse.ArgumentParser() 
parser.add_argument('--load-from-csv', action='store_true') 
args = parser.parse_args() 

def get_initial_count(url, api_key):
	"""Fetch the initial page to get the total count of articles."""
	headers = {'Authorization': api_key}
	try:
		response = requests.get(url, headers=headers)
		response.raise_for_status()  # Ensures HTTPError is raised for bad requests
		data = response.json()
		return data['count']
	except requests.exceptions.RequestException as e:
		print(f"Failed to fetch initial count: {e}")
		return None

def fetch_articles_page(url, api_key, page):
	"""Fetch a single page of articles."""
	params = {'page': page}
	headers = {'Authorization': api_key}
	try:
		response = requests.get(url, headers=headers, params=params)
		response.raise_for_status()
		return response.json()['results']
	except requests.exceptions.RequestException as e:
		print(f"Failed to fetch page {page}: {e}")
		return []

# Util function to clean text
def cleanText(text):
	"""
		Cleans the input text by applying the following:
		
			* change to lowercase text
			* replace common symbols with a space
			* replace invalid symbols with empty char
			* remove stopwords from text
		
		Arguments
		---------
		text: a string
		
		Output
		------
		return: modified initial string
	"""

	# change to lowercase text
	text = text.lower()

	# replace REPLACE_BY_SPACE_RE symbols by space in text. substitute the matched string in REPLACE_BY_SPACE_RE with space.
	text = REPLACE_BY_SPACE_RE.sub(' ', text)

	# remove symbols which are in BAD_SYMBOLS_RE from text. substitute the matched string in BAD_SYMBOLS_RE with nothing. 
	text = BAD_SYMBOLS_RE.sub('', text)

	# remove stopwords from text
	text = ' '.join(word for word in text.split() if word not in STOPWORDS)

	return text

def cleanHTML(input):
	return BeautifulSoup(input, 'html.parser').get_text()

class DenseTransformer(TransformerMixin):
	def fit(self, X, y=None, **fit_params):
		return self

	def transform(self, X, y=None, **fit_params):
		return X.toarray()

# Fetch and download data
def get_articles(url, api_key):
	headers = {'Authorization': api_key}
	try:
		response = requests.get(url, headers=headers)
		if response.status_code == 200:
			return response.json()
		else:
			print(f"Error: Received response code {response.status_code}")
			return None
	except requests.exceptions.RequestException as e:
		print(f"An error occurred: {e}")
		return None

source_data_csv = f"source.csv"
if args.load_from_csv == True: # load data from csv file into a pandas dataframe 
	print("Loading data from source.csv")
	articles_df = pd.read_csv(source_data_csv)
	
else: 
	print("Loading data from API...")
	articles_per_page = 10
	total_articles = get_initial_count(get_api_url, api_key)
	fetch_url = get_api_url
	articles = []
	if not total_articles:
		print("Failed to get the total count of articles.") 
	else:
		total_pages = math.ceil(total_articles / articles_per_page)
		print(f"Total articles: {total_articles}, Total pages: {total_pages}")
		# Concurrently fetch all pages with progress bar
		with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
			futures = {executor.submit(fetch_articles_page, get_api_url, api_key, page): page for page in range(1, total_pages + 1)}
			for future in tqdm(concurrent.futures.as_completed(futures), total=total_pages, desc="Fetching articles"):
				articles.extend(future.result())
		# Save fetched data to CSV
		articles_df = pd.DataFrame(articles)
		file_date = datetime.now().strftime("%Y-%m-%d")
		articles_df.to_csv(source_data_csv, index=False)

# Clean the dataset
articles_df['relevant'] = articles_df['relevant'].fillna(value=0)
# Change true to 1 and false to 0
articles_df['relevant'] = articles_df['relevant'].replace(True, 1)
articles_df['relevant'] = articles_df['relevant'].replace(False, 0)

articles_df["terms"] = articles_df["title"].astype(str) + " " + articles_df["summary"].astype(str)
# Keep only the needed columns
articles_df = articles_df[['relevant', 'terms']]
# The input of each model is the texts for each record
input = articles_df['terms']
# The output of each model is the relevancy tagged for each record
output = articles_df['relevant']

# Divide into train and test sets
X_train, X_test, Y_train, Y_test = train_test_split(
	# The array of inputs
	input,
	# The array of outputs
	output,
	# The size of the testing set in relation to the entire dataset (0.2 = 20%)
	test_size = 0.2,
	# This acts as a seed, to maintain consistency between tests
	random_state = 42,
	# Whether to shuffle the data before splitting, it helps maintain consistency between tests
	shuffle = False
)
# change the type of the Y axis to avoid error further on
Y_train = Y_train.astype(int)
Y_test = Y_test.astype(int)

# These are the pipeline step names
VECTORIZER = 'vectorizer'
CLASSIFIER = 'classifier'

# These are the different model names
GNB = "gnb"
MNB = "mnb"
LR = "lr"
LSVC = "lsvc"

# This is the dict that will store the pipelines
pipelines = {}

# All models will use the same vectorizer
vectorizer = TfidfVectorizer()

# Define a pipeline combining a text feature extractor with a classifier for each model

pipelines[GNB] = Pipeline([
		(VECTORIZER, vectorizer),
		# This intermediate step is required because the GaussianNB
		# model does not work with sparse vectors
		('to_dense', DenseTransformer()),
		(CLASSIFIER, OneVsRestClassifier(GaussianNB())),
		])

pipelines[MNB] = Pipeline([
		(VECTORIZER, vectorizer),
		(CLASSIFIER, OneVsRestClassifier(MultinomialNB(fit_prior=True, class_prior=None))),
		])

pipelines[LR] = Pipeline([
		(VECTORIZER, vectorizer),
		(CLASSIFIER, OneVsRestClassifier(LogisticRegression(solver='sag'), n_jobs=1)),
		])

pipelines[LSVC] = Pipeline([
		(VECTORIZER, vectorizer),
		(CLASSIFIER, OneVsRestClassifier(LinearSVC(), n_jobs=1)),
		])

for model, pipeline in pipelines.items():
	# Train phase
	print("Training the " + model + " model...")
	pipeline.fit(X_train, Y_train)
	
	# Testing accuracy
	prediction = pipeline.predict(X_test)
	accuracy = accuracy_score(Y_test, prediction)
	print(" => Accuracy for the " + model + " model: {:2.1f}%".format(accuracy * 100))


for model, pipeline in pipelines.items():
	# Before saving, let's train the model with the entire dataset first
	print("Training the " + model + " model with the entire dataset...")
	pipeline.fit(input, output)
	# Save the pipeline for later use (`compress` argument is to save as one single file with the entire pipeline)
	dump(pipeline, './model_' + model + '.joblib', compress=1)
