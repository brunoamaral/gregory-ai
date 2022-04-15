
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.multiclass import OneVsRestClassifier
from sklearn.naive_bayes import GaussianNB
from sklearn.naive_bayes import MultinomialNB
from sklearn.metrics import accuracy_score
from sklearn.svm import LinearSVC
from sklearn.linear_model import LogisticRegression
from .utils.model_utils import DenseTransformer
from joblib import dump
from django_cron import CronJobBase, Schedule

class TrainModels(CronJobBase):
	RUN_EVERY_MINS = 1 # every 2 days
	schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
	code = 'gregory.train_models'    # a unique code
	def do(self):
		# The CSV file that has the source data
		SOURCE_DATA_CSV = "/code/gregory/data/source.csv"

		# Let's load the CSV file into a Pandas dataset
		dataset = pd.read_csv(SOURCE_DATA_CSV)

		# Clean relevant column because there still seem to be some NaN values there
		dataset['relevant'] = dataset['relevant'].fillna(value=0)

		# The input of each model is the texts for each record
		input = dataset['terms']
		# The output of each model is the relevancy tagged for each record
		output = dataset['relevant']

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
			dump(pipeline, '/code/gregory/ml_models/model_' + model + '.joblib', compress=1)
	pass
