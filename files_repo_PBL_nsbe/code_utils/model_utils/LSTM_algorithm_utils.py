import tensorflow as tf
from tensorflow.keras.callbacks import EarlyStopping
import time
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import gensim.downloader as api
import joblib
import re
import string

class LSTM_Classifier:
    """
    A class used to represent an LSTM model for text classification.

    ...

    Attributes
    ----------
    vectorize_layer : tf.keras.layers.Layer
        Pre-configured TextVectorization layer.
    embedding_dim : int
        Dimension of the embedding layer.
    lstm_units : int
        Number of units in each LSTM layer.
    num_lstm_layers : int, optional
        Number of LSTM layers in the model (default is 1).
    bidirectional : bool, optional
        Whether to use bidirectional LSTM layers (default is False).
    num_classes : int, optional
        Number of classes to predict (default is 6).
    seed : int, optional
        Random seed for reproducibility (default is 42).
    dropout_rate : float, optional
        Dropout rate for Dropout layers (default is 0.5).
    l2_lambda : float, optional
        L2 regularization parameter (default is 0.01).
    metrics : list, optional
        List of metrics to evaluate the model (default is ['accuracy']).
    batch_normalization : bool, optional
        Whether to include BatchNormalization layers (default is False).
    embedding_name : str, optional
        Name of the pre-trained embedding to load from Gensim (default is None).

    Methods
    -------
    load_pretrained_embeddings(embedding_name='glove-wiki-gigaword-200'):
        Loads pre-trained embeddings using Gensim.

    create_embedding_matrix(vocab, embeddings_index, embedding_dim):
        Creates an embedding matrix for the given vocabulary using pre-trained embeddings.

    create_embedding_layer():
        Creates an embedding layer, either trainable or using pre-trained embeddings.

    create_lstm_model():
        Creates an LSTM model with the specified configuration.

    train_model(train_ds, val_ds, epochs, start_from_epoch=3):
        Trains the LSTM model using the provided training and validation datasets, with early stopping.

    plot_history(history, metrics=['loss', 'accuracy', 'precision', 'recall']):
        Plots the training history.

    evaluate_and_log_model(test_dataset, train_time, n_epochs, model_description='model', metrics=['loss', 'accuracy'], model_registry=None):
        Evaluates the model on the test set and logs the results.

    train_plot_and_evaluate(train_ds, val_ds, test_ds, epochs=20, strt_from_epoch=3, model_description='model', metrics=['loss', 'accuracy'], model_registry=None):
        Trains the model, plots the training history, and evaluates the model on the test set.

    save_model(model_path):
        Saves the model to a file.

    load_model(model_path):
        Loads the model from a file.

    predict(X):
        Predicts labels for new data.
    """
    def __init__(self, vectorize_layer, embedding_dim=200, lstm_units=32, num_lstm_layers=1, bidirectional=False, num_classes=6, seed=42, dropout_rate=0.5, l2_lambda=0.01, metrics=['accuracy'], batch_normalization=False, embedding_name=None):
        """
        Constructs all the necessary attributes for the LSTM_TFIDF_Classifier object.

        Parameters
        ----------
        vectorize_layer : tf.keras.layers.Layer
            Pre-configured TextVectorization layer.
        embedding_dim : int
            Dimension of the embedding layer.
        lstm_units : int
            Number of units in each LSTM layer.
        num_lstm_layers : int, optional
            Number of LSTM layers in the model (default is 1).
        bidirectional : bool, optional
            Whether to use bidirectional LSTM layers (default is False).
        num_classes : int, optional
            Number of classes to predict (default is 6).
        seed : int, optional
            Random seed for reproducibility (default is 42).
        dropout_rate : float, optional
            Dropout rate for Dropout layers (default is 0.5).
        l2_lambda : float, optional
            L2 regularization parameter (default is 0.01).
        metrics : list, optional
            List of metrics to evaluate the model (default is ['accuracy']).
        batch_normalization : bool, optional
            Whether to include BatchNormalization layers (default is False).
        embedding_name : str, optional
            Name of the pre-trained embedding to load from Gensim (default is None)."""
        
        self.vectorize_layer = vectorize_layer
        self.embedding_dim = embedding_dim
        self.lstm_units = lstm_units
        self.num_lstm_layers = num_lstm_layers
        self.bidirectional = bidirectional
        self.num_classes = num_classes
        self.seed = seed
        self.dropout_rate = dropout_rate
        self.l2_lambda = l2_lambda
        self.metrics = metrics
        self.batch_normalization = batch_normalization
        self.embedding_name = embedding_name
        self.model = self.create_lstm_model()
        self.history = None
        self.time_delta = None
        self.train_epochs = None

    def load_pretrained_embeddings(self, embedding_name='glove-wiki-gigaword-200'):
        """
        Load pre-trained embeddings using Gensim.

        Parameters
        ----------
        embedding_name : str, optional
            Name of the pre-trained embedding to load (default is 'glove-wiki-gigaword-200').

        Returns
        -------
        embeddings_index : dict
            Dictionary mapping words to their embeddings.
        embedding_dim : int
            Dimension of the embeddings.
        """
        embeddings_model = api.load(embedding_name)
        embedding_dim = embeddings_model.vector_size
        embeddings_index = {word: embeddings_model[word] for word in embeddings_model.index_to_key}
        return embeddings_index, embedding_dim

    def create_embedding_matrix(self, vocab, embeddings_index, embedding_dim):
        """
        Create an embedding matrix for the given vocabulary using pre-trained embeddings.

        Parameters
        ----------
        vocab : list
            List of words in the vocabulary.
        embeddings_index : dict
            Dictionary mapping words to their embeddings.
        embedding_dim : int
            Dimension of the embeddings.

        Returns
        -------
        embedding_matrix : np.array
            Embedding matrix.
        """
        embedding_matrix = np.zeros((len(vocab) + 1, embedding_dim))
        for i, word in enumerate(vocab):
            embedding_vector = embeddings_index.get(word)
            if embedding_vector is not None:
                embedding_matrix[i + 1] = embedding_vector  # index 0 is reserved for padding
        return embedding_matrix

    def create_embedding_layer(self):
        """
        Create an embedding layer, either trainable or using pre-trained embeddings.

        Returns
        -------
        embedding_layer : tf.keras.layers.Embedding
            Embedding layer.
        """
        if self.embedding_name:
            embeddings_index, embedding_dim = self.load_pretrained_embeddings(self.embedding_name)
            vocab = self.vectorize_layer.get_vocabulary()
            embedding_matrix = self.create_embedding_matrix(vocab, embeddings_index, embedding_dim)
            embedding_layer = tf.keras.layers.Embedding(
                input_dim=embedding_matrix.shape[0],
                output_dim=embedding_matrix.shape[1],
                embeddings_initializer=tf.keras.initializers.Constant(embedding_matrix),
                trainable=False,
                mask_zero=True
            )
        else:
            embedding_layer = tf.keras.layers.Embedding(
                input_dim=self.vectorize_layer.vocabulary_size(),
                output_dim=self.embedding_dim,
                embeddings_initializer=tf.keras.initializers.GlorotUniform(seed=self.seed),
                embeddings_regularizer=tf.keras.regularizers.l2(self.l2_lambda),
                mask_zero=True
            )
        return embedding_layer

    def create_lstm_model(self):
        """
        Create an LSTM model with the specified configuration.

        Returns
        -------
        model : tf.keras.Model
            Compiled LSTM model.
        """
        inputs = tf.keras.Input(name='text', shape=(1,), dtype=tf.string)
        x = self.vectorize_layer(inputs)
        x = self.create_embedding_layer()(x)

        for i in range(self.num_lstm_layers):
            return_sequences = (i < self.num_lstm_layers - 1)
            if self.bidirectional:
                x = tf.keras.layers.Bidirectional(
                    tf.keras.layers.LSTM(
                        self.lstm_units,
                        return_sequences=return_sequences,
                        kernel_initializer=tf.keras.initializers.GlorotUniform(seed=self.seed),
                        recurrent_initializer=tf.keras.initializers.Orthogonal(seed=self.seed),
                        kernel_regularizer=tf.keras.regularizers.l2(self.l2_lambda),
                        recurrent_regularizer=tf.keras.regularizers.l2(self.l2_lambda)
                    )
                )(x)
            else:
                x = tf.keras.layers.LSTM(
                    self.lstm_units,
                    return_sequences=return_sequences,
                    kernel_initializer=tf.keras.initializers.GlorotUniform(seed=self.seed),
                    recurrent_initializer=tf.keras.initializers.Orthogonal(seed=self.seed),
                    kernel_regularizer=tf.keras.regularizers.l2(self.l2_lambda),
                    recurrent_regularizer=tf.keras.regularizers.l2(self.l2_lambda)
                )(x)
            if self.batch_normalization:
                x = tf.keras.layers.BatchNormalization()(x)
            x = tf.keras.layers.Dropout(self.dropout_rate, seed=self.seed)(x)

        if self.num_classes == 1:
            activation = 'sigmoid'
            loss = 'binary_crossentropy'
        else:
            activation = 'softmax'
            loss = 'categorical_crossentropy'

        outputs = tf.keras.layers.Dense(
            self.num_classes,
            activation=activation,
            kernel_initializer=tf.keras.initializers.GlorotUniform(seed=self.seed),
            kernel_regularizer=tf.keras.regularizers.l2(self.l2_lambda)
        )(x)

        model = tf.keras.Model(inputs=inputs, outputs=outputs)
        model.compile(optimizer='adam', loss=loss, metrics=self.metrics)
        return model

    def train_model(self, train_ds, val_ds, epochs, start_from_epoch=3):
        """
        Trains the LSTM model using the provided training and validation datasets, with early stopping.

        Parameters
        ----------
        train_ds : tf.data.Dataset
            Training dataset.
        val_ds : tf.data.Dataset
            Validation dataset.
        epochs : int
            Number of epochs to train the model.
        start_from_epoch : int, optional
            Epoch number from which early stopping is considered (default is 3).

        Returns
        -------
        history : tf.keras.callbacks.History
            History object containing the training history.
        time_delta : float
            Total time taken for the training.
        train_epochs : int
            Total number of epochs the model was trained for.
        """
        early_stopping = EarlyStopping(
            monitor='val_loss',
            mode='min',
            patience=3,
            restore_best_weights=True,
            verbose=1,
            start_from_epoch=start_from_epoch
        )

        start_time = time.time()
        history = self.model.fit(
            train_ds,
            validation_data=val_ds,
            epochs=epochs,
            verbose=1,
            callbacks=[early_stopping]
        )
        end_time = time.time()
        time_delta = end_time - start_time
        train_epochs = len(history.history['loss'])

        self.history = history
        self.time_delta = time_delta
        self.train_epochs = train_epochs

        return history, time_delta, train_epochs

    def plot_history(self, metrics=['accuracy', 'precision', 'recall', 'AUC']):
        """
        Plot the training history.

        Parameters
        ----------
        history : tf.keras.callbacks.History
            History object containing the training history.
        metrics : list, optional
            List of metrics to plot (default is ['loss', 'accuracy', 'precision', 'recall', 'AUC']).
        """
        metrics = ['loss'] + metrics
        num_metrics = len(metrics)
        plt.figure(figsize=(8 * num_metrics // 2, 5 * num_metrics // 2))

        for i, metric in enumerate(metrics):
            plt.subplot((num_metrics + 1) // 2, 2, i + 1)
            plt.plot(self.history.history[metric], label=f'Training {metric.capitalize()}')
            plt.plot(self.history.history[f'val_{metric}'], label=f'Validation {metric.capitalize()}')
            plt.title(f'{metric.capitalize()} Over Epochs')
            plt.legend()
            plt.xlabel('Epochs')
            plt.ylabel(metric.capitalize())

        plt.tight_layout()
        plt.show()

        for metric in metrics:
            final_train_value = self.history.history[metric][-1]
            final_val_value = self.history.history[f'val_{metric}'][-1]
            print(f"\nFinal Training {metric.capitalize()}: {final_train_value:.4f}")
            print(f"Final Validation {metric.capitalize()}: {final_val_value:.4f}")

    def evaluate_and_log_model(self, test_dataset, train_time, n_epochs, model_description='model', metrics=['accuracy', 'recall', 'precision', 'AUC'], model_registry=None):
        """
        Evaluate the model on the test set and log the results.

        Parameters
        ----------
        test_dataset : tf.data.Dataset
            Test dataset.
        train_time : float
            Total time taken for the training.
        n_epochs : int
            Number of epochs the model was trained for.
        model_description : str, optional
            Description of the model (default is 'model').
        metrics : list, optional
            List of metrics to evaluate (default is ['accuracy', 'recall', 'precision', 'AUC']).
        model_registry : pd.DataFrame, optional
            DataFrame containing the results of previous models (default is None).

        Returns
        -------
        model_registry : pd.DataFrame
            Updated DataFrame containing the results of all models.
        """
        metrics = ['loss'] + metrics
        result_dict = self.model.evaluate(test_dataset, return_dict=True)
        print(result_dict)
        log_data = {
            'model': model_description,
            'training_time': train_time,
            'n_epochs': n_epochs,
            'avg_epoch_time': train_time / n_epochs
        }

        for metric in metrics:
            log_data[f'test_{metric}'] = result_dict[metric]

        new_row = pd.DataFrame([log_data])

        for metric in metrics:
            print(f"Test {metric.capitalize()}: {result_dict[metric]}")

        if model_registry is None:
            print('*** INITIALISING RESULTS TABLE ***')
            columns = ['model', 'training_time', 'n_epochs', 'avg_epoch_time'] + [f'test_{metric}' for metric in metrics]
            model_registry = pd.DataFrame(columns=columns)

        model_registry = pd.concat([model_registry, new_row], ignore_index=True)
        model_registry.to_csv('model_results_table.csv', index=False)
        return model_registry

    def train_plot_and_evaluate(self, train_ds, val_ds, test_ds, epochs=20, strt_from_epoch=3, model_description='model', metrics_plot=['loss', 'accuracy'], metrics_eval=['accuracy'], model_registry=None):
        """
        Trains the model, plots the training history, and evaluates the model on the test set.

        Parameters
        ----------
        train_ds : tf.data.Dataset
            Training dataset.
        val_ds : tf.data.Dataset
            Validation dataset.
        test_ds : tf.data.Dataset
            Test dataset.
        epochs : int, optional
            Number of epochs to train the model (default is 20).
        strt_from_epoch : int, optional
            Epoch number from which early stopping is considered (default is 3).
        model_description : str, optional
            Description of the model (default is 'model').
        metrics : list, optional
            List of metrics to evaluate (default is ['loss', 'accuracy']).
        model_registry : pd.DataFrame, optional
            DataFrame containing the results of previous models (default is None).

        Returns
        -------
        model_registry : pd.DataFrame
            Updated DataFrame containing the results of all models.
        """
        print('*** INITIALIZING MODEL TRAINING ***\n\n')
        history, train_time, n_epochs = self.train_model(train_ds, val_ds, epochs, start_from_epoch=strt_from_epoch)
        print('\n\n*** TRAINING COMPLETE ***\n\n')
        print('*** PLOTTING TRAINING HISTORY ***\n\n')
        self.plot_history(history, metrics_plot)
        print('\n\n*** EVALUATING MODEL ON TEST SET ***\n\n')
        model_registry = self.evaluate_and_log_model(test_ds, train_time, n_epochs, model_description, metrics_eval, model_registry)
        return model_registry

    def save_model(self, model_path):
        """
        Save the model to a file.

        Parameters
        ----------
        model_path : str
            Path to save the model.
        """
        self.model.save(model_path)
        print(f"Model saved at {model_path}")

    def load_model(self, model_path):
        """
        Load the model from a file.

        Parameters
        ----------
        model_path : str
            Path to load the model from.
        """
        self.model = tf.keras.models.load_model(model_path)
        print(f"Model loaded from {model_path}")

    def predict(self, X):
        """
        Predict labels for new data.

        Parameters
        ----------
        X : np.ndarray
            New data to predict.

        Returns
        -------
        np.ndarray
            Predicted labels.
        """
        return self.model.predict(X)

    def simple_evaluate(self, test_dataset, metrics=['accuracy', 'recall', 'precision', 'AUC']):
        """
        Evaluate the model on the test set and return the specified metrics.

        Parameters
        ----------
        test_dataset : tf.data.Dataset
            Test dataset.
        metrics : list, optional
            List of metrics to evaluate (default is ['accuracy', 'recall', 'precision', 'AUC']).

        Returns
        -------
        dict
            Dictionary with the specified metrics and their values.
        """
        result_dict = self.model.evaluate(test_dataset, return_dict=True)
        return {metric: result_dict[metric] for metric in metrics}

# Example usage:
# vectorize_layer = ...  # your pre-configured TextVectorization layer
# lstm_tfidf = LSTM_Classifier(vectorize_layer, embedding_dim=128, lstm_units=64, num_classes=6, embedding_name='glove-wiki-gigaword-200')
# train_ds = ...  # your training dataset
# val_ds = ...  # your validation dataset
# test_ds = ...  # your test dataset
# lstm_tfidf.train_plot_and_evaluate(train_ds, val_ds, test_ds)
# lstm_tfidf.save_model('lstm_model.h5')
# lstm_tfidf.load_model('lstm_model.h5')
# predictions = lstm_tfidf.predict(new_data)



def custom_standardization(input_text):
    """Lowercase and remove punctuation from the text."""
    lowercase_text = tf.strings.lower(input_text)
    cleaned_text = tf.strings.regex_replace(lowercase_text, '[%s]' % re.escape(string.punctuation), '')
    return cleaned_text


def get_lstm_vectoriser(train_data_path, function):
    train = pd.read_csv(train_data_path)

    output_dim = 512

    vocab_size = 22000

    # Create the TextVectorization layer
    vectorize_layer = tf.keras.layers.TextVectorization(
        max_tokens=vocab_size,
        output_mode='int',
        output_sequence_length=output_dim,
        standardize=function
    )

    # Assuming `texts` is a list or dataset of your text data
    vectorize_layer.adapt(train['text_processed'])
    return vectorize_layer
