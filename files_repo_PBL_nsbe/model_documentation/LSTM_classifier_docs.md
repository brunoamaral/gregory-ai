# LSTM_Classifier

A class used to represent an LSTM model for text classification.

## Attributes

- **vectorize_layer** : `tf.keras.layers.Layer`
  Pre-configured TextVectorization layer.
- **embedding_dim** : `int`
  Dimension of the embedding layer.
- **lstm_units** : `int`
  Number of units in each LSTM layer.
- **num_lstm_layers** : `int`, optional
  Number of LSTM layers in the model (default is 1).
- **bidirectional** : `bool`, optional
  Whether to use bidirectional LSTM layers (default is False).
- **num_classes** : `int`, optional
  Number of classes to predict (default is 6).
- **seed** : `int`, optional
  Random seed for reproducibility (default is 42).
- **dropout_rate** : `float`, optional
  Dropout rate for Dropout layers (default is 0.5).
- **l2_lambda** : `float`, optional
  L2 regularization parameter (default is 0.01).
- **metrics** : `list`, optional
  List of metrics to evaluate the model (default is ['accuracy']).
- **batch_normalization** : `bool`, optional
  Whether to include BatchNormalization layers (default is False).
- **embedding_name** : `str`, optional
  Name of the pre-trained embedding to load from Gensim (default is None).

## Methods

### load_pretrained_embeddings(embedding_name='glove-wiki-gigaword-200')

Loads pre-trained embeddings using Gensim.

**Parameters**
- **embedding_name** : `str`, optional
  Name of the pre-trained embedding to load (default is 'glove-wiki-gigaword-200').

**Returns**
- **embeddings_index** : `dict`
  Dictionary mapping words to their embeddings.
- **embedding_dim** : `int`
  Dimension of the embeddings.

### create_embedding_matrix(vocab, embeddings_index, embedding_dim)

Creates an embedding matrix for the given vocabulary using pre-trained embeddings.

**Parameters**
- **vocab** : `list`
  List of words in the vocabulary.
- **embeddings_index** : `dict`
  Dictionary mapping words to their embeddings.
- **embedding_dim** : `int`
  Dimension of the embeddings.

**Returns**
- **embedding_matrix** : `np.array`
  Embedding matrix.

### create_embedding_layer()

Creates an embedding layer, either trainable or using pre-trained embeddings.

**Returns**
- **embedding_layer** : `tf.keras.layers.Embedding`
  Embedding layer.

### create_lstm_model()

Creates an LSTM model with the specified configuration.

**Returns**
- **model** : `tf.keras.Model`
  Compiled LSTM model.

### train_model(train_ds, val_ds, epochs, start_from_epoch=3)

Trains the LSTM model using the provided training and validation datasets, with early stopping.

**Parameters**
- **train_ds** : `tf.data.Dataset`
  Training dataset.
- **val_ds** : `tf.data.Dataset`
  Validation dataset.
- **epochs** : `int`
  Number of epochs to train the model.
- **start_from_epoch** : `int`, optional
  Epoch number from which early stopping is considered (default is 3).

**Returns**
- **history** : `tf.keras.callbacks.History`
  History object containing the training history.
- **time_delta** : `float`
  Total time taken for the training.
- **train_epochs** : `int`
  Total number of epochs the model was trained for.

### plot_history(history, metrics=['loss', 'accuracy', 'precision', 'recall'])

Plot the training history.

**Parameters**
- **history** : `tf.keras.callbacks.History`
  History object containing the training history.
- **metrics** : `list`, optional
  List of metrics to plot (default is ['loss', 'accuracy', 'precision', 'recall']).

### evaluate_and_log_model(test_dataset, train_time, n_epochs, model_description='model', metrics=['loss', 'accuracy'], model_registry=None)

Evaluate the model on the test set and log the results.

**Parameters**
- **test_dataset** : `tf.data.Dataset`
  Test dataset.
- **train_time** : `float`
  Total time taken for the training.
- **n_epochs** : `int`
  Number of epochs the model was trained for.
- **model_description** : `str`, optional
  Description of the model (default is 'model').
- **metrics** : `list`, optional
  List of metrics to evaluate (default is ['loss', 'accuracy']).
- **model_registry** : `pd.DataFrame`, optional
  DataFrame containing the results of previous models (default is None).

**Returns**
- **model_registry** : `pd.DataFrame`
  Updated DataFrame containing the results of all models.

### train_plot_and_evaluate(train_ds, val_ds, test_ds, epochs=20, strt_from_epoch=3, model_description='model', metrics=['loss', 'accuracy'], model_registry=None)

Trains the model, plots the training history, and evaluates the model on the test set.

**Parameters**
- **train_ds** : `tf.data.Dataset`
  Training dataset.
- **val_ds** : `tf.data.Dataset`
  Validation dataset.
- **test_ds** : `tf.data.Dataset`
  Test dataset.
- **epochs** : `int`, optional
  Number of epochs to train the model (default is 20).
- **strt_from_epoch** : `int`, optional
  Epoch number from which early stopping is considered (default is 3).
- **model_description** : `str`, optional
  Description of the model (default is 'model').
- **metrics** : `list`, optional
  List of metrics to evaluate (default is ['loss', 'accuracy']).
- **model_registry** : `pd.DataFrame`, optional
  DataFrame containing the results of previous models (default is None).

**Returns**
- **model_registry** : `pd.DataFrame`
  Updated DataFrame containing the results of all models.

### save_model(model_path)

Save the model to a file.

**Parameters**
- **model_path** : `str`
  Path to save the model.

### load_model(model_path)

Load the model from a file.

**Parameters**
- **model_path** : `str`
  Path to load the model from.

### predict(X)

Predict labels for new data.

**Parameters**
- **X** : `np.ndarray`
  New data to predict.

**Returns**
- **np.ndarray**
  Predicted labels.

### simple_evaluate(test_dataset, metrics=['accuracy', 'recall', 'precision', 'AUC'])

Evaluate the model on the test set and return the specified metrics.

**Parameters**
- **test_dataset** : `tf.data.Dataset`
  Test dataset.
- **metrics** : `list`, optional
  List of metrics to evaluate (default is ['accuracy', 'recall', 'precision', 'AUC']).

**Returns**
- **dict**
  Dictionary with the specified metrics and their values.


# Example usage

```python
import pandas as pd
import tensorflow as tf
import string
import re

# Load datasets
train = pd.read_csv('data/pseudo_bert_train.csv')
val = pd.read_csv('data/pseudo_bert_val.csv')
test = pd.read_csv('data/pseudo_bert_test.csv')

# Prepare TensorFlow datasets
train_ds = tf.data.Dataset.from_tensor_slices((train['text_processed'].values, train['relevant'].values))
val_ds = tf.data.Dataset.from_tensor_slices((val['text_processed'].values, val['relevant'].values))
test_ds = tf.data.Dataset.from_tensor_slices((test['text_processed'].values, test['relevant'].values))

train_ds = train_ds.batch(32)
val_ds = val_ds.batch(32)
test_ds = test_ds.batch(32)

AUTOTUNE = tf.data.AUTOTUNE

train_ds = train_ds.cache().prefetch(buffer_size=AUTOTUNE)
val_ds = val_ds.cache().prefetch(buffer_size=AUTOTUNE)
test_ds = test_ds.cache().prefetch(buffer_size=AUTOTUNE)

# Define TextVectorization layer
output_dim = 512
vocab_size = 22000

def custom_standardization(input_text):
    """Lowercase and remove punctuation from the text."""
    lowercase_text = tf.strings.lower(input_text)
    cleaned_text = tf.strings.regex_replace(lowercase_text, '[%s]' % re.escape(string.punctuation), '')
    return cleaned_text

vectorize_layer = tf.keras.layers.TextVectorization(
    max_tokens=vocab_size,
    output_mode='int',
    output_sequence_length=output_dim,
    standardize=custom_standardization
)

vectorize_layer.adapt(train['text_processed'])

# Initialize and use the model
seed = 42
metrics = ['accuracy', 'recall', 'precision', 'AUC']

lstm_tfidf = LSTM_TFIDF_Classifier(
    vectorize_layer=vectorize_layer,
    embedding_dim=200,
    lstm_units=32,
    num_lstm_layers=1,
    num_classes=1,
    seed=seed,
    dropout_rate=0.3,
    l2_lambda=0.001,
    metrics=metrics,
    bidirectional=True
)

lstm_tfidf.train_plot_and_evaluate(train_ds, val_ds, test_ds)
lstm_tfidf.save_model('lstm_model.h5')
lstm_tfidf.load_model('lstm_model.h5')
predictions = lstm_tfidf.predict(test_ds)

# Simple evaluation
results = lstm_tfidf.simple_evaluate(test_ds, metrics)
print(results)
```

# Important Notes

## Code for Vectorization Layer

Before creating the `LSTM_TFIDF_Classifier`, you need to define and adapt the `TextVectorization` layer. Here's an example of how to set up the vectorization layer:

```python
import tensorflow as tf
import string
import re

output_dim = 512
vocab_size = 22000

def custom_standardization(input_text):
    """Lowercase and remove punctuation from the text."""
    lowercase_text = tf.strings.lower(input_text)
    cleaned_text = tf.strings.regex_replace(lowercase_text, '[%s]' % re.escape(string.punctuation), '')
    return cleaned_text

# Create the TextVectorization layer
vectorize_layer = tf.keras.layers.TextVectorization(
    max_tokens=vocab_size,
    output_mode='int',
    output_sequence_length=output_dim,
    standardize=custom_standardization
)

# Assuming `texts` is a list or dataset of your text data
vectorize_layer.adapt(train['text_processed'])
```

## Using Pre-trained Embeddings or Trainable Embeddings
The LSTM_TFIDF_Classifier provides an option to use either pre-trained embeddings from Gensim or trainable embeddings of a specified size.

### Pre-trained Embeddings
To use pre-trained embeddings, specify the embedding_name parameter when initializing the LSTM_TFIDF_Classifier. This will load the embeddings using Gensim and create an embedding matrix accordingly.

```python
Copy code
lstm_tfidf = LSTM_TFIDF_Classifier(
    vectorize_layer=vectorize_layer,
    embedding_dim=200,  # This value is ignored when using pre-trained embeddings
    lstm_units=32,
    num_lstm_layers=1,
    num_classes=1,
    seed=seed,
    dropout_rate=0.3,
    l2_lambda=0.001,
    metrics=metrics,
    bidirectional=True,
    embedding_name='glove-wiki-gigaword-200'
)
```
### Trainable Embeddings
If you prefer to use trainable embeddings, set the embedding_name parameter to None (default). The embedding_dim parameter will define the size of the embedding layer.

```python
Copy code
lstm_tfidf = LSTM_TFIDF_Classifier(
    vectorize_layer=vectorize_layer,
    embedding_dim=200,  # Size of the trainable embeddings
    lstm_units=32,
    num_lstm_layers=1,
    num_classes=1,
    seed=seed,
    dropout_rate=0.3,
    l2_lambda=0.001,
    metrics=metrics,
    bidirectional=True
)
```

