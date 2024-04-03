import os
import html
import pandas as pd
from datetime import date
from joblib import load
from django.core.management.base import BaseCommand
from django.utils import timezone
from gregory.models import Articles
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
        articles_queryset = Articles.objects.filter(ml_prediction_gnb=None, summary__gt=50)
        dataset = pd.DataFrame(list(articles_queryset.values("title", "summary", "relevant", "article_id")))

        if not dataset.empty:
            dataset["title"] = dataset["title"].apply(cleanText)
            dataset["summary"] = dataset["summary"].apply(html.unescape).apply(cleanHTML).apply(cleanText)
            dataset["terms"] = dataset["title"].astype(str) + " " + dataset["summary"].astype(str)
            dataset = dataset[["terms", "relevant", "article_id"]]
            dataset["relevant"] = dataset["relevant"].fillna(value=0).astype(int)
            dataset.to_csv(dataset_file_csv, index=False)

            # Load the data back
            dataset = pd.read_csv(dataset_file_csv)
            dataset['relevant'] = dataset['relevant'].fillna(value=0)

            # Prediction
            for model in models:
                print(f"Predicting relevancy using {model}...")
                for _, row in dataset.iterrows():
                    prediction = pipelines[model].predict([row['terms']])
                    article = Articles.objects.get(pk=row['article_id'])
                    if prediction[0] == 1:
                        setattr(article, f'ml_prediction_{model}', True)
                    else:
                        setattr(article, f'ml_prediction_{model}', False)
                    print(article)
                    article.save()
