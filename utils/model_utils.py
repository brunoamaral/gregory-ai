from sklearn.base import TransformerMixin
import numpy as np

# This is an util function to be used in the GaussianNB pipeline
class DenseTransformer():
	def fit(self, X, y=None, **fit_params):
		return self

	def transform(self, X, y=None, **fit_params):
		return np.asarray(X.todense())
