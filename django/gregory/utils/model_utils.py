from sklearn.base import TransformerMixin

# This is an util function to be used in the GaussianNB pipeline
class DenseTransformer(TransformerMixin):
    def fit(self, X, y=None, **fit_params):
        return self
    def transform(self, X, y=None, **fit_params):
        return X.todense()