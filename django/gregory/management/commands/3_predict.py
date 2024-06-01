import os
import html
import pandas as pd
from datetime import date
from joblib import load
from django.core.management.base import BaseCommand
from django.utils import timezone
from gregory.models import Articles, Subject, MLPredictions, Team
from gregory.utils.model_utils import DenseTransformer
from gregory.utils.text_utils import cleanHTML, cleanText
import feedparser
import requests
from dateutil.parser import parse
from dateutil.tz import gettz
from django.db.models import Q, Exists, OuterRef


class Command(BaseCommand):
	help = 'Run prediction models on articles.'

	def handle(self, *args, **options):
		# Initialize models and load pipelines
		models = ["gnb", "lsvc", "mnb", "lr"]

		today = date.today()

		# Fetch new data
		subjects = Subject.objects.all()
		for subject in subjects:
            # Load pipelines if model files exist
			pipelines = {}
			for model in models:
				model_path = f'/code/gregory/ml_models/team_id_{subject.team.pk}/subject_id_{subject.pk}/model_{model}.joblib'
				if os.path.exists(model_path):
					pipelines[model] = load(model_path)
				else:
					print(f"Model file not found: {model_path}")
					continue

			# Define a queryset for MLPredictions related to the specific subject
			predictions_for_subject = MLPredictions.objects.filter(
				subject=subject,
				articles=OuterRef('pk')
			)
			# Query articles where there is no matching MLPredictions entry for the subject
			articles_queryset = Articles.objects.annotate(
				has_predictions=Exists(predictions_for_subject)
			).filter(
				has_predictions=False,  # Filter articles that do not have predictions for the subject
				summary__gt=50,    # Ensure summary length greater than 50 characters
				subjects=subject        # Match the specific subject
			).distinct()

			dataset = pd.DataFrame(list(articles_queryset.values("title", "summary", "relevant", "article_id")))

			if not dataset.empty:
				dataset["title"] = dataset["title"].apply(cleanText)
				dataset["summary"] = dataset["summary"].apply(html.unescape).apply(cleanHTML).apply(cleanText)
				dataset["terms"] = dataset["title"].astype(str) + " " + dataset["summary"].astype(str)
				dataset = dataset[["terms", "relevant", "article_id"]]
				dataset["relevant"] = dataset["relevant"].fillna(value=0).astype(int)
				dataset['relevant'] = dataset['relevant'].fillna(value=0)

				# Iterate through the dataset
				for _, row in dataset.iterrows():
					article = Articles.objects.get(pk=row['article_id'])
					new_predictions = {}
					new_predictions['subject'] = subject

					# Run predictions for each model and store in new_predictions dict
					for model in models:
						if model in pipelines:  # Check if the model was successfully loaded
							prediction = pipelines[model].predict([row['terms']])
							new_predictions[f'{model}'] = prediction[0] == 1
						else:
							print(f"Skipping predictions for model '{model}' as it was not loaded.")

					# Create a new MLPrediction object and save it
					ml_prediction_instance = MLPredictions(
						**new_predictions  # Unpack predictions dictionary to fields
					)
					ml_prediction_instance.save()

					# Link the new prediction to the article and save the article
					article.ml_predictions.add(ml_prediction_instance)
					article.save()

					print(f'Predictions saved for Article ID {article.pk}')
