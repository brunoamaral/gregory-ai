# BERT Classifier Documentation

## Overview

The `BERT_Classifier` class is used to represent a BERT model for text classification. It includes methods for encoding texts, creating the model, training, plotting, evaluating, saving, and loading the model, as well as predicting new data and calculating token lengths.

## Class: `BERT_Classifier`

### Attributes

- **max_len**: `int`
  - Maximum length of the input sequences.
- **tokenizer**: `transformers.BertTokenizer`
  - BERT tokenizer.
- **bert_model**: `transformers.TFBertModel`
  - BERT model.
- **best_learning_rate**: `float`
  - Best learning rate for the optimizer.
- **best_dense_units**: `int`
  - Number of units in the dense layer.
- **best_freeze_weights**: `bool`
  - Whether to freeze BERT weights during training.
- **model**: `tf.keras.Model`
  - Compiled BERT model.
- **history**: `tf.keras.callbacks.History`
  - History object containing training history.
- **time_delta**: `float`
  - Time taken for training.
- **train_epochs**: `int`
  - Number of epochs the model was trained for.

### Methods

#### `__init__(self, max_len=128, bert_model_name='microsoft/BiomedNLP-BiomedBERT-base-uncased-abstract-fulltext', best_learning_rate=2e-05, best_dense_units=48, best_freeze_weights=False)`

Initializes the BERT_Classifier with the given parameters.

#### `encode_texts(self, texts)`

Encodes texts using the BERT tokenizer.

**Parameters:**
- `texts`: List of strings to be encoded.

**Returns:**
- `input_ids`: Tensor of input IDs.
- `attention_masks`: Tensor of attention masks.

#### `create_bert_model(self)`

Creates a BERT model with the specified configuration.

**Returns:**
- `model`: Compiled Keras model.

#### `train_model(self, train_inputs, train_labels, val_inputs, val_labels, epochs=10)`

Trains the BERT model.

**Parameters:**
- `train_inputs`: Training input data.
- `train_labels`: Training labels.
- `val_inputs`: Validation input data.
- `val_labels`: Validation labels.
- `epochs`: Number of epochs to train the model.

**Returns:**
- `history`: Training history.
- `time_delta`: Time taken for training.
- `train_epochs`: Number of epochs the model was trained for.

#### `plot_history(self, metrics=['accuracy', 'loss'])`

Plots the training history.

**Parameters:**
- `metrics`: List of metrics to plot (default is `['accuracy', 'loss']`).

#### `evaluate_and_log_model(self, test_inputs, test_labels, train_time, n_epochs, model_description='model', metrics=['accuracy'], model_registry=None)`

Evaluates the model and logs the results.

**Parameters:**
- `test_inputs`: Test input data.
- `test_labels`: Test labels.
- `train_time`: Time taken for training.
- `n_epochs`: Number of epochs the model was trained for.
- `model_description`: Description of the model (default is `'model'`).
- `metrics`: List of metrics to evaluate (default is `['accuracy']`).
- `model_registry`: DataFrame to log the results.

**Returns:**
- `model_registry`: Updated DataFrame with the evaluation results.

#### `train_plot_and_evaluate(self, train_inputs, train_labels, val_inputs, val_labels, test_inputs, test_labels, epochs=10, model_description='model', metrics=['accuracy'], model_registry=None)`

Trains, plots, and evaluates the model.

**Parameters:**
- `train_inputs`: Training input data.
- `train_labels`: Training labels.
- `val_inputs`: Validation input data.
- `val_labels`: Validation labels.
- `test_inputs`: Test input data.
- `test_labels`: Test labels.
- `epochs`: Number of epochs to train the model.
- `model_description`: Description of the model (default is `'model'`).
- `metrics`: List of metrics to evaluate (default is `['accuracy']`).
- `model_registry`: DataFrame to log the results.

**Returns:**
- `model_registry`: Updated DataFrame with the evaluation results.

#### `save_model(self, model_path)`

Saves the model.

**Parameters:**
- `model_path`: Path to save the model.

#### `load_model(self, model_path)`

Loads the model.

**Parameters:**
- `model_path`: Path to load the model from.

#### `predict(self, X)`

Predicts labels for new data.

**Parameters:**
- `X`: Input data.

**Returns:**
- Predictions for the input data.

#### `simple_evaluate(self, test_inputs, test_labels, metrics=['accuracy'])`

Evaluates the model and returns specified metrics.

**Parameters:**
- `test_inputs`: Test input data.
- `test_labels`: Test labels.
- `metrics`: List of metrics to evaluate (default is `['accuracy']`).

**Returns:**
- Dictionary of evaluated metrics.

#### `calculate_and_plot_token_lengths(self, train_df, val_df, test_df)`

Calculates and plots the token lengths for the given dataframes.

**Parameters:**
- `train_df`: DataFrame containing training data.
- `val_df`: DataFrame containing validation data.
- `test_df`: DataFrame containing test data.

## Example Usage

```python
# Load the data
train_df = pd.read_csv('pseudo_bert_train.csv')
val_df = pd.read_csv('pseudo_bert_val.csv')
test_df = pd.read_csv('pseudo_bert_test.csv')

# Create an instance of BERT_Classifier
bert_classifier = BERT_Classifier()

# Calculate and plot token lengths
bert_classifier.calculate_and_plot_token_lengths(train_df, val_df, test_df)

# Encode the datasets
X_train_ids, X_train_masks = bert_classifier.encode_texts(train_df['text_processed'].values)
X_val_ids, X_val_masks = bert_classifier.encode_texts(val_df['text_processed'].values)
X_test_ids, X_test_masks = bert_classifier.encode_texts(test_df['text_processed'].values)

# One-hot encode the labels
y_train = to_categorical(train_df['relevant'], num_classes=2)
y_val = to_categorical(val_df['relevant'], num_classes=2)
y_test = to_categorical(test_df['relevant'], num_classes=2)

# Train, plot, and evaluate the model
model_registry = bert_classifier.train_plot_and_evaluate([X_train_ids, X_train_masks], y_train, [X_val_ids, X_val_masks], y_val, [X_test_ids, X_test_masks], y_test)
