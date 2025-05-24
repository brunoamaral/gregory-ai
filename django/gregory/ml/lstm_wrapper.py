"""
LSTM implementation for text classification.

This module provides the LSTMTrainer class for training, evaluating, and saving
LSTM-based models for text classification.
"""
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union, Any, Callable

import numpy as np
import pandas as pd
import tensorflow as tf
from tensorflow.keras.layers import (
    TextVectorization,
    Embedding, 
    LSTM, 
    Bidirectional, 
    Dense, 
    Dropout,
    BatchNormalization
)
from tensorflow.keras.models import Sequential, Model
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.callbacks import EarlyStopping
from tensorflow.keras.metrics import Precision, Recall, AUC
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
    average_precision_score
)


class LSTMTrainer:
    """
    Trainer for LSTM text classification models.
    
    This class handles the creation, training, evaluation, and saving of LSTM-based
    text classification models.
    
    Attributes:
        max_tokens (int): Maximum number of words in the vocabulary
        sequence_length (int): Maximum length of input sequences
        embedding_dim (int): Dimension of word embeddings
        lstm_units (int): Number of LSTM units
        dropout_rate (float): Dropout rate for regularization
        learning_rate (float): Learning rate for the optimizer
        bidirectional (bool): Whether to use bidirectional LSTM
        batch_normalization (bool): Whether to use batch normalization
        random_state (int): Random seed for reproducibility
        metrics (List[str]): List of metrics to compute
        vectorizer (TextVectorization): The text vectorizer
        model (tf.keras.Model): The LSTM model
        history (tf.keras.callbacks.History): Training history
        training_time (float): Time taken for training in seconds
        epochs_trained (int): Number of epochs the model was trained for
    """
    
    def __init__(
        self,
        max_tokens: int = 10000,
        sequence_length: int = 100,
        embedding_dim: int = 128,
        lstm_units: int = 64,
        dropout_rate: float = 0.3,
        learning_rate: float = 0.001,
        bidirectional: bool = True,
        batch_normalization: bool = False,
        random_state: int = 69,
        metrics: Optional[List[str]] = None
    ):
        """
        Initialize a new LSTMTrainer.
        
        Args:
            max_tokens (int, optional): Maximum vocabulary size. Defaults to 10000.
            sequence_length (int, optional): Maximum sequence length. Defaults to 100.
            embedding_dim (int, optional): Embedding dimension. Defaults to 128.
            lstm_units (int, optional): Number of LSTM units. Defaults to 64.
            dropout_rate (float, optional): Dropout rate. Defaults to 0.3.
            learning_rate (float, optional): Learning rate. Defaults to 0.001.
            bidirectional (bool, optional): Whether to use bidirectional LSTM. Defaults to True.
            batch_normalization (bool, optional): Whether to use batch normalization. Defaults to False.
            random_state (int, optional): Random seed. Defaults to 69.
            metrics (Optional[List[str]], optional): Metrics to track. 
                Defaults to None, which will use ['accuracy', 'precision', 'recall', 'auc'].
        """
        # Set random seeds for reproducibility
        tf.random.set_seed(random_state)
        np.random.seed(random_state)
        
        self.max_tokens = max_tokens
        self.sequence_length = sequence_length
        self.embedding_dim = embedding_dim
        self.lstm_units = lstm_units
        self.dropout_rate = dropout_rate
        self.learning_rate = learning_rate
        self.bidirectional = bidirectional
        self.batch_normalization = batch_normalization
        self.random_state = random_state
        
        # Default metrics if none provided
        if metrics is None:
            self.metrics_names = ['accuracy', 'precision', 'recall', 'auc']
        else:
            self.metrics_names = metrics
        
        # Initialize vectorizer
        self.vectorizer = TextVectorization(
            max_tokens=max_tokens,
            output_sequence_length=sequence_length,
            standardize=self._custom_standardization
        )
        
        # Initialize model (will be created after vectorizer is adapted)
        self.model = None
        
        # Training attributes
        self.history = None
        self.training_time = None
        self.epochs_trained = None
    
    def _custom_standardization(self, input_text: tf.Tensor) -> tf.Tensor:
        """
        Custom standardization function for text preprocessing.
        
        Args:
            input_text (tf.Tensor): Input text tensor
        
        Returns:
            tf.Tensor: Standardized text tensor
        """
        # Convert to lowercase and remove punctuation
        lowercase = tf.strings.lower(input_text)
        # Remove punctuation - fixed version that doesn't use escape_bytes which is missing in TF 2.15.0
        punctuation = '!"#$%&()*+,-./:;<=>?@[\\]^_`{|}~\\t\\n'
        return tf.strings.regex_replace(lowercase, f'[{punctuation}]', '')
    
    def _create_model(self) -> tf.keras.Model:
        """
        Create an LSTM-based classification model.
        
        Returns:
            tf.keras.Model: The compiled model.
        """
        # Get vocabulary size after adaptation
        vocab_size = len(self.vectorizer.get_vocabulary())
        
        # Create model
        model = Sequential()
        
        # Add embedding layer
        model.add(Embedding(
            input_dim=vocab_size,
            output_dim=self.embedding_dim,
            input_length=self.sequence_length
        ))
        
        # Add LSTM layer with dropout
        if self.bidirectional:
            lstm_layer = Bidirectional(LSTM(self.lstm_units, return_sequences=False))
            model.add(lstm_layer)
        else:
            lstm_layer = LSTM(self.lstm_units, return_sequences=False)
            model.add(lstm_layer)
        
        # Add dropout for regularization
        model.add(Dropout(self.dropout_rate))
        
        # Add batch normalization if enabled
        if self.batch_normalization:
            model.add(BatchNormalization())
        
        # Add output layer
        model.add(Dense(1, activation='sigmoid'))
        
        # Configure metrics
        metrics = ['accuracy']
        if 'precision' in self.metrics_names:
            metrics.append(Precision(name='precision'))
        if 'recall' in self.metrics_names:
            metrics.append(Recall(name='recall'))
        if 'auc' in self.metrics_names:
            metrics.append(AUC(name='auc'))
        
        # Compile model
        model.compile(
            optimizer=Adam(learning_rate=self.learning_rate),
            loss='binary_crossentropy',
            metrics=metrics
        )
        
        return model
    
    def train(
        self,
        train_texts: List[str],
        train_labels: List[int],
        val_texts: List[str],
        val_labels: List[int],
        epochs: int = 10,
        batch_size: int = 32
    ) -> tf.keras.callbacks.History:
        """
        Train the LSTM model.
        
        Args:
            train_texts (List[str]): Training text data
            train_labels (List[int]): Training labels (0/1)
            val_texts (List[str]): Validation text data
            val_labels (List[int]): Validation labels (0/1)
            epochs (int, optional): Maximum number of epochs. Defaults to 10.
            batch_size (int, optional): Batch size for training. Defaults to 32.
            
        Returns:
            tf.keras.callbacks.History: Training history object
        """
        # Adapt vectorizer to the training data
        print("Adapting text vectorizer to training data...")
        train_text_ds = tf.data.Dataset.from_tensor_slices(train_texts).batch(batch_size)
        self.vectorizer.adapt(train_text_ds)
        
        # Create model after adaptation
        self.model = self._create_model()
        print(f"Model created with vocabulary size: {len(self.vectorizer.get_vocabulary())}")
        
        # Prepare early stopping callback
        early_stopping = EarlyStopping(
            monitor='val_loss',
            mode='min',
            patience=3,
            restore_best_weights=True
        )
        
        # Vectorize the input texts
        X_train = self.vectorizer(np.array(train_texts))
        X_val = self.vectorizer(np.array(val_texts))
        
        # Convert labels to numpy arrays
        y_train = np.array(train_labels, dtype=np.float32)
        y_val = np.array(val_labels, dtype=np.float32)
        
        # Record start time
        start_time = time.time()
        
        # Train the model
        print(f"Training LSTM model for up to {epochs} epochs...")
        history = self.model.fit(
            X_train,
            y_train,
            validation_data=(X_val, y_val),
            epochs=epochs,
            batch_size=batch_size,
            callbacks=[early_stopping]
        )
        
        # Record training time and epochs
        self.training_time = time.time() - start_time
        self.epochs_trained = len(history.history['loss'])
        self.history = history
        
        print(f"Training completed in {self.training_time:.2f} seconds ({self.epochs_trained} epochs).")
        
        return history
    
    def evaluate(
        self,
        test_texts: List[str],
        test_labels: List[int],
        threshold: float = 0.8
    ) -> Dict[str, Union[float, Dict]]:
        """
        Evaluate the LSTM model on test data.
        
        Args:
            test_texts (List[str]): Test text data
            test_labels (List[int]): Test labels (0/1)
            threshold (float, optional): Probability threshold for positive class.
                Defaults to 0.8.
                
        Returns:
            Dict[str, Union[float, Dict]]: Dictionary of evaluation metrics:
                - accuracy: Accuracy score
                - precision: Precision score
                - recall: Recall score
                - f1: F1 score
                - roc_auc: ROC AUC score
                - pr_auc: Precision-Recall AUC score
                - confusion_matrix: Confusion matrix
                - classification_report: Classification report as a dictionary
        """
        if self.model is None:
            raise ValueError("Model has not been trained yet.")
        
        # Vectorize test texts
        X_test = self.vectorizer(np.array(test_texts))
        y_test = np.array(test_labels)
        
        # Get predictions
        predictions_prob = self.model.predict(X_test).flatten()
        
        # Apply threshold to get binary predictions
        predictions = (predictions_prob >= threshold).astype(int)
        
        # Calculate metrics
        metrics = {
            "accuracy": accuracy_score(test_labels, predictions),
            "precision": precision_score(test_labels, predictions, average='binary'),
            "recall": recall_score(test_labels, predictions, average='binary'),
            "f1": f1_score(test_labels, predictions, average='binary'),
            "roc_auc": roc_auc_score(test_labels, predictions_prob),
            "pr_auc": average_precision_score(test_labels, predictions_prob),
            "confusion_matrix": confusion_matrix(test_labels, predictions).tolist(),
            "classification_report": classification_report(test_labels, predictions, output_dict=True)
        }
        
        return metrics
    
    def predict(
        self,
        texts: List[str],
        threshold: float = 0.8
    ) -> Tuple[List[int], List[float]]:
        """
        Make predictions on new data.
        
        Args:
            texts (List[str]): List of texts to classify
            threshold (float, optional): Probability threshold for positive class.
                Defaults to 0.8.
                
        Returns:
            Tuple[List[int], List[float]]: Tuple of (predictions, probabilities),
                where predictions are binary labels and probabilities are for the positive class
        """
        if self.model is None:
            raise ValueError("Model has not been trained yet.")
        
        # Vectorize texts
        X = self.vectorizer(np.array(texts))
        
        # Get predictions
        probabilities = self.model.predict(X).flatten().tolist()
        
        # Apply threshold
        predictions = [1 if prob >= threshold else 0 for prob in probabilities]
        
        return predictions, probabilities
    
    def save(self, model_dir: Union[str, Path]) -> Dict[str, str]:
        """
        Save the model and vectorizer.
        
        Args:
            model_dir (Union[str, Path]): Directory to save model artifacts
            
        Returns:
            Dict[str, str]: Dictionary with paths to saved artifacts
        """
        if self.model is None:
            raise ValueError("No model to save.")
        
        # Ensure model_dir is a Path object
        model_dir = Path(model_dir)
        model_dir.mkdir(parents=True, exist_ok=True)
        
        # Save model weights
        weights_path = model_dir / "lstm_weights.h5"
        self.model.save_weights(str(weights_path))
        
        # Save vectorizer config and vocabulary
        vectorizer_path = model_dir / "tokenizer.json"
        
        # Get the config but handle the standardize function which is not JSON serializable
        raw_config = self.vectorizer.get_config()
        
        # Create a copy of the config that we can modify
        serializable_config = raw_config.copy()
        
        # Replace the non-serializable standardize function with a string identifier
        if callable(serializable_config.get('standardize')):
            serializable_config['standardize'] = "custom_standardization"
            
        # Handle any other potentially non-serializable objects
        for key, value in serializable_config.items():
            # Check if the value is JSON serializable
            try:
                json.dumps({key: value})
            except (TypeError, OverflowError):
                # If not serializable, convert to a string representation
                if key != 'standardize':  # We already handled standardize
                    serializable_config[key] = str(value)
        
        vectorizer_config = {
            "config": serializable_config,
            "vocabulary": self.vectorizer.get_vocabulary(),
            "index_word": {i: word for i, word in enumerate(self.vectorizer.get_vocabulary())}
        }
        
        with open(vectorizer_path, 'w') as f:
            json.dump(vectorizer_config, f, indent=2)
        
        # Save metrics and parameters
        metrics_path = model_dir / "metrics.json"
        
        # Extract training history if available
        history_metrics = {}
        if self.history is not None:
            history_metrics = {
                "final_loss": self.history.history['loss'][-1],
                "final_val_loss": self.history.history['val_loss'][-1],
            }
            
            # Include other metrics
            for metric in self.metrics_names:
                if metric in self.history.history:
                    history_metrics[f"final_{metric}"] = self.history.history[metric][-1]
                    history_metrics[f"final_val_{metric}"] = self.history.history[f"val_{metric}"][-1]
        
        metrics_info = {
            "max_tokens": self.max_tokens,
            "sequence_length": self.sequence_length,
            "embedding_dim": self.embedding_dim,
            "lstm_units": self.lstm_units,
            "dropout_rate": self.dropout_rate,
            "learning_rate": self.learning_rate,
            "bidirectional": self.bidirectional,
            "batch_normalization": self.batch_normalization,
            "training_time_seconds": self.training_time,
            "epochs_trained": self.epochs_trained,
            "vocabulary_size": len(self.vectorizer.get_vocabulary()),
            "timestamp": datetime.now().isoformat(),
            "model_params": {
                "max_tokens": self.max_tokens,
                "sequence_length": self.sequence_length,
                "embedding_dim": self.embedding_dim,
                "lstm_units": self.lstm_units,
                "dropout_rate": self.dropout_rate,
                "learning_rate": self.learning_rate,
                "bidirectional": self.bidirectional,
                "batch_normalization": self.batch_normalization,
            },
            **history_metrics
        }
        
        with open(metrics_path, 'w') as f:
            json.dump(metrics_info, f, indent=2)
        
        return {
            "weights_path": str(weights_path),
            "vectorizer_path": str(vectorizer_path),
            "metrics_path": str(metrics_path)
        }
    
    def load(self, model_dir: Union[str, Path]) -> None:
        """
        Load the model and vectorizer from disk.
        
        Args:
            model_dir (Union[str, Path]): Directory containing model artifacts
        """
        model_dir = Path(model_dir)
        
        weights_path = model_dir / "lstm_weights.h5"
        vectorizer_path = model_dir / "tokenizer.json"
        
        if not weights_path.exists() or not vectorizer_path.exists():
            raise FileNotFoundError(f"Model files not found in {model_dir}")
        
        # Load vectorizer configuration and vocabulary
        with open(vectorizer_path, 'r') as f:
            vectorizer_data = json.load(f)
        
        # Create a new TextVectorization layer with our desired settings
        # rather than trying to deserialize the exact configuration
        self.vectorizer = TextVectorization(
            max_tokens=self.max_tokens,
            output_sequence_length=self.sequence_length,
            standardize=self._custom_standardization
        )
        
        # Set vocabulary from the saved state
        vocabulary = vectorizer_data["vocabulary"]
        self.vectorizer.set_vocabulary(vocabulary)
        
        # Create model architecture
        self.model = self._create_model()
        
        # Load weights
        self.model.load_weights(str(weights_path))
        
        print(f"Loaded model and vectorizer from {model_dir}")
    
    def export_metrics_json(
        self,
        val_metrics: Dict[str, float],
        test_metrics: Dict[str, float],
        output_path: Union[str, Path]
    ) -> None:
        """
        Export metrics to a JSON file according to the required format.
        
        Args:
            val_metrics (Dict[str, float]): Validation metrics dictionary
            test_metrics (Dict[str, float]): Test metrics dictionary
            output_path (Union[str, Path]): Path to save the metrics JSON
        """
        # Format metrics with val_ and test_ prefixes
        metrics_dict = {}
        
        # Add validation metrics
        for k, v in val_metrics.items():
            if isinstance(v, (int, float)):  # Only include numeric metrics
                metrics_dict[f"val_{k}"] = v
        
        # Add test metrics
        for k, v in test_metrics.items():
            if isinstance(v, (int, float)):  # Only include numeric metrics
                metrics_dict[f"test_{k}"] = v
        
        # Add model parameters
        metrics_dict.update({
            "max_tokens": self.max_tokens,
            "sequence_length": self.sequence_length,
            "embedding_dim": self.embedding_dim,
            "lstm_units": self.lstm_units,
            "dropout_rate": self.dropout_rate,
            "learning_rate": self.learning_rate,
            "bidirectional": self.bidirectional,
            "batch_normalization": self.batch_normalization,
            "training_time_seconds": self.training_time,
            "epochs_trained": self.epochs_trained,
            "timestamp": datetime.now().isoformat(),
        })
        
        # Save metrics to file
        with open(output_path, 'w') as f:
            json.dump(metrics_dict, f, indent=2)
        
        print(f"Metrics saved to {output_path}")
