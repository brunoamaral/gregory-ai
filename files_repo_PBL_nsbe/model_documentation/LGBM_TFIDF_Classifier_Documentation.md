
# LGBM_TFIDF_Classifier

A utility file for LGBM + TFIDF algorithm for classification consisting of training function, prediction function, and evaluation function.

## Attributes

- **vectorizer** : `sklearn.feature_extraction.text.TfidfVectorizer`
  TF-IDF vectorizer.
- **classifier** : `lightgbm.LGBMClassifier`
  LightGBM classifier.
- **fitted** : `bool`
  Whether the model has been trained.

## Methods

### __init__(lgbm_params=None, random_seed=42)

Initialize the classifier with LGBM parameters and random seed.

**Parameters**
- **lgbm_params** : `dict`, optional
  Parameters for the LGBM classifier (default is None).
- **random_seed** : `int`, optional
  Random seed for reproducibility (default is 42).

### train(X, y)

Train the LGBM classifier with TF-IDF vectorizer.

**Parameters**
- **X** : `pd.Series` or `list`
  Text data.
- **y** : `pd.Series` or `list`
  Labels.

### evaluate(X, y)

Evaluate the model with accuracy, recall, precision, f1, ROC AUC, classification report, and confusion matrix.

**Parameters**
- **X** : `pd.Series` or `list`
  Text data.
- **y** : `pd.Series` or `list`
  True labels.

**Returns**
- **dict**
  Dictionary with accuracy, recall, precision, f1, ROC AUC, classification report, and confusion matrix.

### predict(X)

Predict labels for new data.

**Parameters**
- **X** : `pd.Series` or `list`
  Text data.

**Returns**
- **np.ndarray**
  Predicted labels.

### save_model(vectorizer_path, classifier_path)

Save the vectorizer and classifier to files.

**Parameters**
- **vectorizer_path** : `str`
  Path to save the vectorizer.
- **classifier_path** : `str`
  Path to save the classifier.

### load_model(vectorizer_path, classifier_path)

Load the vectorizer and classifier from files.

**Parameters**
- **vectorizer_path** : `str`
  Path to load the vectorizer from.
- **classifier_path** : `str`
  Path to load the classifier from.

## Example usage

```python
import pandas as pd
from sklearn.externals import joblib

# Load your data
train = pd.read_csv('train_data.csv')
test = pd.read_csv('test_data.csv')

# Initialize and use the model
lgbm_params = {
    'n_estimators': 100,
    'learning_rate': 0.1,
    'num_leaves': 31
}

classifier = LGBM_TFIDF_Classifier(lgbm_params=lgbm_params, random_seed=42)

# Train the model
X_train = train['text']
y_train = train['label']
classifier.train(X_train, y_train)

# Evaluate the model
X_test = test['text']
y_test = test['label']
results = classifier.evaluate(X_test, y_test)
print(results)

# Predict new data
predictions = classifier.predict(X_test)

# Save the model
classifier.save_model('tfidf_vectorizer.pkl', 'lgbm_classifier.pkl')

# Load the model
classifier.load_model('tfidf_vectorizer.pkl', 'lgbm_classifier.pkl')
```
