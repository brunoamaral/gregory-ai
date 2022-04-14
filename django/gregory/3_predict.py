from joblib import load
from tony.model_utils import DenseTransformer
from sys import argv
from datetime import date
import pandas as pd
import html
from tony.text_utils import cleanHTML
from tony.text_utils import cleanText
from joblib import load
from pandas.io.json import json_normalize #package for flattening json in pandas df
from models import Articles
from django_cron import CronJobBase, Schedule

class RunPredictor(CronJobBase):
	RUN_EVERY_MINS = 120 # every 2 hours
	schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
	code = 'gregory.predict'    # a unique code
	def do(self):    
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

		dataset = pd.DataFrame(list(Articles.objects.filter(ml_prediction_gnb=None).values("title", "summary", "relevant", "article_id")[:2]))
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
						"prediction": str(int(prediction))
					})
			return result,data

		data = predictor(dataset)
		for model in data[0]['models']:
			for item in data[0]['models'][model]:
				print(model)
				article = Articles.objects.get(pk=item['article_id'])
				print(article,model,item['prediction'])
				if item['prediction'] == '1':
					if model == 'gnb':
						article.ml_prediction_gnb = True
					if model == 'lr':
						article.ml_prediction_lr = True
				if item['prediction'] == '0':
					if model == 'gnb':
						article.ml_prediction_gnb = False
					if model == 'lr':
						article.ml_prediction_lr = False
				article.save()
