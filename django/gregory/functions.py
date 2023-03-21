from crossref.restful import Works, Etiquette
from sitesettings.models import CustomSetting
import re
import os
from joblib import load
from .utils.model_utils import DenseTransformer
from datetime import date
import pandas as pd
import html
from .utils.text_utils import cleanHTML
from .utils.text_utils import cleanText
from joblib import load
from .models import Articles
from django_cron import CronJobBase, Schedule
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

def remove_utm(url):
	u = urlparse(url)
	query = parse_qs(u.query, keep_blank_values=True)
	query.pop('utm_source', None)
	query.pop('utm_medium', None)
	query.pop('utm_campaign', None)
	query.pop('utm_content', None)
	u = u._replace(query=urlencode(query, True))
	return urlunparse(u)


def get_doi(title):
	doi = None
	if title != '':
		i = 0
	site = CustomSetting.objects.get(site__domain=os.environ.get('DOMAIN_NAME'))
	client_website = 'https://' + site.site.domain + '/'
	my_etiquette = Etiquette(site.title, 'v8', client_website, site.admin_email)
	works = Works(etiquette=my_etiquette)
	work = works.query(bibliographic=title).sort('relevance')
	for w in work:
		if 'title' in w:
			crossref_title = ''
			article_title = re.sub(r'[^A-Za-z0-9 ]+', '', title)
			article_title = re.sub(r' ','',article_title ).lower()
			crossref_title = re.sub(r'[^A-Za-z0-9 ]+', '', w['title'][0])
			crossref_title = re.sub(r' ','',crossref_title).lower()
			if crossref_title == article_title:
				doi = w['DOI']
				return doi
			i += 1
			if i == 5:
				return None

def predict(articles=Articles.objects.filter(ml_prediction_gnb=None,summary__gt=50).values("title", "summary", "relevant", "article_id")):
	# These are the different model names
	GNB = "gnb"
	LSVC = "lsvc"
	MNB = "mnb"
	LR = "lr"

	models = [GNB, LSVC, MNB, LR]

	# This is the dict that will store the pipelines
	pipelines = {}

	for model in models:
		pipelines[model] = load('/code/gregory/ml_models/model_' + model + '.joblib')

	# Now let's fetch a new set of data
	today = date.today()
	# year_month = today.strftime("%Y/%m")

	# dataset_file_json = '/code/gregory/data/' + today.strftime("%Y-%B") + '.json'
	dataset_file_csv = '/code/gregory/data/' + today.strftime("%Y-%B") + '.csv'
	dataset = pd.DataFrame(list(articles.values()))
	if len(dataset) > 0:
		# i think we don't need the line below
		# dataset = pd.json_normalize(data=data['results'])
		# KeyError: 'results'


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

		# We no longer need some text columns, let's remove them
		dataset = dataset[["terms", "relevant", "article_id"]]

		# There are several records in the "relevant" column as NaN. Let's convert them to zeros
		dataset["relevant"] = dataset["relevant"].fillna(value=0)

		# A value is trying to be set on a copy of a slice from a DataFrame.
		# Try using .loc[row_indexer,col_indexer] = value instead

		# See the caveats in the documentation: https://pandas.pydata.org/pandas-docs/stable/user_guide/indexing.html#returning-a-view-versus-a-copy

		# change true/false to 1/0
		dataset["relevant"] = dataset["relevant"].astype(int)

		# Save the dataset
		dataset.to_csv(dataset_file_csv, index=False)

		# Load the source data into a Pandas dataframe
		dataset = pd.read_csv(dataset_file_csv)

		# Replace any NaN with zero
		dataset['relevant'] = dataset['relevant'].fillna(value=0)

		# Models to use from the list above
		models = [GNB,LR]

		# This is the dict that will store the pipelines
		pipelines = {}

		for model in models:
			pipelines[model] = load('/code/gregory/ml_models/model_' + model + '.joblib')

		def predictor(dataset):
			data = {"O": []}
			result = {}
			result["models"] = {}
			for model in models:
				data[model] = []    
				result["models"][model] = []
			for index, row in dataset.iterrows():
				input = row['terms']
				output = row['relevant']
				data["O"].append(output)
				test = "TEST " + str(index + 1) + " (provided: " + str(int(output)) + ")"
				for model in models:
					prediction = pipelines[model].predict([input])
					data[model].append(prediction)
					test += " - " + model + ": " + str(prediction)
					result["models"][model].append({
						"article_id": row['article_id'],
						"prediction": str(prediction)
					})
			return result,data

		data = predictor(dataset)
		for model in data[0]['models']:
			for item in data[0]['models'][model]:
				# print(model)
				article = Articles.objects.get(pk=item['article_id'])
				# print(article,model,item['prediction'],type(item['prediction']))
				if item['prediction'] == '1' or item['prediction'] == "['True']":
					if model == 'gnb':
						article.ml_prediction_gnb = True
					if model == 'lr':
						article.ml_prediction_lr = True
				if item['prediction'] == "['0']":
					if model == 'gnb':
						article.ml_prediction_gnb = False
					if model == 'lr':
						article.ml_prediction_lr = False
				article.save()
	return
