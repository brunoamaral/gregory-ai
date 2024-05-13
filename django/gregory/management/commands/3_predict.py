import os
import html
import pandas as pd
from datetime import date
from joblib import load
from django.core.management.base import BaseCommand
from django.utils import timezone
from gregory.models import Articles, Subject, MLPredictions
from gregory.utils.model_utils import DenseTransformer
from gregory.utils.text_utils import cleanHTML, cleanText
import feedparser
import requests
from dateutil.parser import parse
from dateutil.tz import gettz

class Command(BaseCommand):
	help = 'Run prediction models on articles.'

	def handle(self, *args, **options):
		# Initialize models and load pipelines
		models = ["gnb", "lsvc", "mnb", "lr"]
		pipelines = {model: load(f'/code/gregory/ml_models/model_{model}.joblib') for model in models}

		today = date.today()
		dataset_file_csv = f'/code/gregory/data/{today.strftime("%Y-%B")}.csv'

		# Fetch new data
		subjects = Subject.objects.all()
		for subject in subjects:
			print(subject)
			articles_queryset = Articles.objects.filter(ml_predictions__isnull=True, summary__gt=50, subjects=subject)
			dataset = pd.DataFrame(list(articles_queryset.values("title", "summary", "relevant", "article_id")))

			if not dataset.empty:
				dataset["title"] = dataset["title"].apply(cleanText)
				dataset["summary"] = dataset["summary"].apply(html.unescape).apply(cleanHTML).apply(cleanText)
				dataset["terms"] = dataset["title"].astype(str) + " " + dataset["summary"].astype(str)
				dataset = dataset[["terms", "relevant", "article_id"]]
				dataset["relevant"] = dataset["relevant"].fillna(value=0).astype(int)
				dataset.to_csv(dataset_file_csv, index=False)
				dataset['relevant'] = dataset['relevant'].fillna(value=0)

			# Iterate through the dataset
			for _, row in dataset.iterrows():
				article = Articles.objects.get(pk=row['article_id'])
				new_predictions = {}
				new_predictions['subject'] = subject
				# Run predictions for each model and store in new_predictions dict
				for model in models:
					prediction = pipelines[model].predict([row['terms']])
					new_predictions[f'{model}'] = prediction[0] == 1
					print(new_predictions)
				# Create a new MLPrediction object and save it
				ml_prediction_instance = MLPredictions(
					**new_predictions  # Unpack predictions dictionary to fields
				)
				ml_prediction_instance.save()

				# Link the new prediction to the article and save the article
				article.ml_predictions.add(ml_prediction_instance)
				print(article)
				article.save()

				print(f'Predictions saved for Article ID {article.pk}')