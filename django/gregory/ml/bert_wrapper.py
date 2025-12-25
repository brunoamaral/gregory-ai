"""
PubMed BERT implementation for text classification.

This module provides the BertTrainer class for training, evaluating, and saving
BERT-based models for text classification, specifically using Microsoft's BiomedNLP
PubMed BERT model.
"""
from datetime import datetime
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union, Any

import numpy as np
import pandas as pd

# Configure GPU memory growth BEFORE other TensorFlow imports
from gregory.ml.gpu_config import configure_gpu_memory_growth
configure_gpu_memory_growth()

import tensorflow as tf
try:
    # Try to use tf-keras for backward compatibility with transformers
    import tf_keras as keras
    from tf_keras.callbacks import EarlyStopping
    from tf_keras.layers import Dense, Dropout, Input
    from tf_keras.metrics import AUC, Precision, Recall
    from tf_keras.models import Model
    from tf_keras.optimizers import Adam
    from tf_keras.regularizers import l2
    from tf_keras.utils import to_categorical
except ImportError:
    # Fall back to TensorFlow's keras if tf-keras is not available
    from tensorflow.keras.callbacks import EarlyStopping
    from tensorflow.keras.layers import Dense, Dropout, Input
    from tensorflow.keras.metrics import AUC, Precision, Recall
    from tensorflow.keras.models import Model
    from tensorflow.keras.optimizers import Adam
    from tensorflow.keras.regularizers import l2
    from tensorflow.keras.utils import to_categorical
from transformers import AutoTokenizer, TFAutoModel

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


class BertTrainer:
    """
    Trainer for PubMed BERT text classification models.
    
    This class handles the creation, training, evaluation and saving of BERT-based 
    text classification models, using the Microsoft BiomedNLP-BiomedBERT base model.
    
    Attributes:
        max_len (int): Maximum sequence length for the BERT tokenizer
        tokenizer (transformers.PreTrainedTokenizer): The BERT tokenizer
        bert_model (transformers.TFPreTrainedModel): The base BERT model
        learning_rate (float): Learning rate for the Adam optimizer
        dense_units (int): Number of units in the dense layer after BERT
        freeze_weights (bool): Whether to freeze the base BERT weights
        model (tf.keras.Model): The compiled Keras model
        history (tf.keras.callbacks.History): Training history
        training_time (float): Time taken for training in seconds
        epochs_trained (int): Number of epochs the model was trained for
        metrics_names (List[str]): Names of the metrics being tracked
    """

    def __init__(
        self,
        max_len: int = 400,
        bert_model_name: str = 'microsoft/BiomedNLP-BiomedBERT-base-uncased-abstract-fulltext',
        learning_rate: float = 2e-5,
        dense_units: int = 48,
        freeze_weights: bool = False,
        metrics: Optional[List[str]] = None
    ):
        """
        Initialize a new BertTrainer.
        
        Args:
            max_len (int, optional): Maximum sequence length. Defaults to 400.
            bert_model_name (str, optional): HuggingFace model identifier. 
                Defaults to 'microsoft/BiomedNLP-BiomedBERT-base-uncased-abstract-fulltext'.
            learning_rate (float, optional): Learning rate. Defaults to 2e-5.
            dense_units (int, optional): Units in dense layer. Defaults to 48.
            freeze_weights (bool, optional): Whether to freeze BERT weights. Defaults to False.
            metrics (Optional[List[str]], optional): Metrics to track. 
                Defaults to None, which will use ['accuracy', 'precision', 'recall', 'auc'].
        """
        self.max_len = max_len
        self.bert_model_name = bert_model_name
        self.learning_rate = learning_rate
        self.dense_units = dense_units
        self.freeze_weights = freeze_weights
        
        # Default metrics if none provided
        if metrics is None:
            self.metrics_names = ['accuracy', 'precision', 'recall', 'auc']
        else:
            self.metrics_names = metrics
            
        # Initialize BERT components
        self.tokenizer = AutoTokenizer.from_pretrained(bert_model_name)
        self.bert_model = TFAutoModel.from_pretrained(bert_model_name, from_pt=True)
        
        # Create and compile the model
        self.model = self._create_model()
        
        # Tracking attributes for training
        self.history = None
        self.training_time = None
        self.epochs_trained = None
    
    def _create_model(self) -> tf.keras.Model:
        """
        Create a BERT-based classification model.
        
        Returns:
            tf.keras.Model: The compiled model.
        """
        # Input layers
        input_ids = Input(shape=(self.max_len,), dtype=tf.int32, name="input_ids")
        attention_masks = Input(shape=(self.max_len,), dtype=tf.int32, name="attention_masks")
        
        # Set whether the BERT model is trainable
        self.bert_model.trainable = not self.freeze_weights
        
        # Get the BERT outputs
        # [0] = sequence output, [1] = pooled output
        bert_output = self.bert_model(input_ids, attention_mask=attention_masks)[0]
        
        # Use the CLS token output (first token)
        cls_token = bert_output[:, 0, :]
        
        # Create a classification head
        x = Dense(self.dense_units, activation='relu', kernel_regularizer=l2(0.01))(cls_token)
        x = Dropout(0.3)(x)
        output = Dense(2, activation='softmax')(x)
        
        # Build the full model
        model = Model(inputs=[input_ids, attention_masks], outputs=output)
        
        # Compile the model with appropriate metrics
        metrics = ['accuracy']
        if 'precision' in self.metrics_names:
            metrics.append(Precision(name='precision'))
        if 'recall' in self.metrics_names:
            metrics.append(Recall(name='recall'))
        if 'auc' in self.metrics_names:
            metrics.append(AUC(name='auc'))
        
        model.compile(
            optimizer=Adam(learning_rate=self.learning_rate),
            loss='categorical_crossentropy',
            metrics=metrics
        )
        
        return model
    
    def encode_texts(self, texts: List[str]) -> Tuple[tf.Tensor, tf.Tensor]:
        """
        Encode text inputs for BERT processing.
        
        Args:
            texts (List[str]): List of text strings to encode
            
        Returns:
            Tuple[tf.Tensor, tf.Tensor]: Tuple of (input_ids, attention_masks)
        """
        input_ids = []
        attention_masks = []
        
        for text in texts:
            # Encode each text with padding and truncation
            encoded = self.tokenizer.encode_plus(
                text,
                add_special_tokens=True,
                max_length=self.max_len,
                truncation=True,
                padding='max_length',
                return_attention_mask=True,
                return_tensors='tf'
            )
            
            # Extract and collect the encodings
            input_ids.append(tf.squeeze(encoded['input_ids']))
            attention_masks.append(tf.squeeze(encoded['attention_mask']))
        
        # Stack the encodings into single tensors
        input_ids = tf.stack(input_ids, axis=0)
        attention_masks = tf.stack(attention_masks, axis=0)
        
        return input_ids, attention_masks
    
    def train(
        self, 
        train_texts: List[str], 
        train_labels: List[int],
        val_texts: List[str], 
        val_labels: List[int],
        epochs: int = 10,
        batch_size: int = 16
    ) -> tf.keras.callbacks.History:
        """
        Train the BERT model.
        
        Args:
            train_texts (List[str]): Training text data
            train_labels (List[int]): Training labels (0/1)
            val_texts (List[str]): Validation text data
            val_labels (List[int]): Validation labels (0/1)
            epochs (int, optional): Maximum number of epochs. Defaults to 10.
            batch_size (int, optional): Batch size for training. Defaults to 16.
            
        Returns:
            tf.keras.callbacks.History: Training history object
        """
        # Prepare early stopping callback
        early_stopping = EarlyStopping(
            monitor='val_loss',
            mode='min',
            patience=3,
            restore_best_weights=True
        )
        
        # Encode the text data
        train_inputs = self.encode_texts(train_texts)
        val_inputs = self.encode_texts(val_texts)
        
        # One-hot encode the labels
        train_labels_onehot = to_categorical(train_labels, num_classes=2)
        val_labels_onehot = to_categorical(val_labels, num_classes=2)
        
        # Record start time for training
        start_time = time.time()
        
        # Train the model
        history = self.model.fit(
            train_inputs,
            train_labels_onehot,
            validation_data=(val_inputs, val_labels_onehot),
            epochs=epochs,
            batch_size=batch_size,
            callbacks=[early_stopping]
        )
        
        # Record end time and calculate duration
        end_time = time.time()
        self.training_time = end_time - start_time
        self.epochs_trained = len(history.history['loss'])
        self.history = history
        
        return history
    
    def evaluate(
        self, 
        test_texts: List[str], 
        test_labels: List[int],
        threshold: float = 0.8
    ) -> Dict[str, Union[float, Dict]]:
        """
        Evaluate the BERT model on test data.
        
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
        
        # Encode the test data
        test_inputs = self.encode_texts(test_texts)
        test_labels_onehot = to_categorical(test_labels, num_classes=2)
        
        # Get predictions
        predictions_prob = self.model.predict(test_inputs)
        
        # Apply threshold to get binary predictions
        predictions = (predictions_prob[:, 1] >= threshold).astype(int)
        
        # Calculate metrics
        metrics = {
            "accuracy": accuracy_score(test_labels, predictions),
            "precision": precision_score(test_labels, predictions, average='binary'),
            "recall": recall_score(test_labels, predictions, average='binary'),
            "f1": f1_score(test_labels, predictions, average='binary'),
            "roc_auc": roc_auc_score(test_labels, predictions_prob[:, 1]),
            "pr_auc": average_precision_score(test_labels, predictions_prob[:, 1]),
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
        
        # Encode the texts
        inputs = self.encode_texts(texts)
        
        # Get predictions
        predictions_prob = self.model.predict(inputs)
        
        # Extract probabilities for the positive class
        positive_probs = predictions_prob[:, 1].tolist()
        
        # Apply threshold
        predictions = [1 if prob >= threshold else 0 for prob in positive_probs]
        
        return predictions, positive_probs
    
    def save(self, model_dir: Union[str, Path], save_format: str = 'h5') -> Dict[str, Any]:
        """
        Save the model and related artifacts.
        
        Args:
            model_dir (Union[str, Path]): Directory to save model artifacts
            save_format (str, optional): Format for saving model weights. Defaults to 'h5'.
            
        Returns:
            Dict[str, Any]: Dictionary of save information
        """
        if self.model is None:
            raise ValueError("No model to save.")
        
        # Ensure model_dir is a Path object
        model_dir = Path(model_dir)
        model_dir.mkdir(parents=True, exist_ok=True)
        
        # Save model weights
        weights_path = model_dir / f"bert_weights.{save_format}"
        self.model.save_weights(str(weights_path), save_format=save_format)
        
        # Save metrics if available
        metrics_info = {}
        if self.history is not None:
            metrics_path = model_dir / "metrics.json"
            
            # Extract validation metrics
            val_metrics = {f"val_{k}": v[-1] for k, v in self.history.history.items()
                           if k.startswith("val_")}
            
            # Add training information
            metrics_info = {
                "bert_model_name": self.bert_model_name,
                "max_len": self.max_len,
                "learning_rate": self.learning_rate,
                "dense_units": self.dense_units,
                "freeze_weights": self.freeze_weights,
                "training_time_seconds": self.training_time,
                "epochs_trained": self.epochs_trained,
                "timestamp": datetime.now().isoformat(),
                **val_metrics
            }
            
            # Save metrics to file
            with open(metrics_path, 'w') as f:
                json.dump(metrics_info, f, indent=2)
        
        return {
            "weights_path": str(weights_path),
            "metrics_info": metrics_info
        }

    def load_weights(self, weights_path: Union[str, Path]) -> None:
        """
        Load trained model weights.
        
        Args:
            weights_path (Union[str, Path]): Path to saved weights file
        """
        self.model.load_weights(str(weights_path))
        print(f"Loaded model weights from {weights_path}")
    
    def perform_pseudo_labeling(
        self,
        labeled_texts: List[str],
        labeled_labels: List[int],
        unlabeled_texts: List[str],
        val_texts: List[str],
        val_labels: List[int],
        confidence_threshold: float = 0.9,
        max_iterations: int = 7,
        batch_size: int = 16,
        epochs_per_iter: int = 3
    ) -> Tuple[List[str], List[int]]:
        """
        Perform pseudo-labeling using self-training.
        
        Args:
            labeled_texts (List[str]): Initial labeled training texts
            labeled_labels (List[int]): Initial labeled training labels (0/1)
            unlabeled_texts (List[str]): Unlabeled texts for pseudo-labeling
            val_texts (List[str]): Validation texts (remains fixed)
            val_labels (List[int]): Validation labels (remains fixed)
            confidence_threshold (float, optional): Minimum confidence to accept a pseudo-label.
                Defaults to 0.9.
            max_iterations (int, optional): Maximum number of pseudo-labeling iterations.
                Defaults to 7.
            batch_size (int, optional): Batch size for training. Defaults to 16.
            epochs_per_iter (int, optional): Epochs to train in each iteration. Defaults to 3.
            
        Returns:
            Tuple[List[str], List[int]]: Enhanced training dataset (texts, labels)
        """
        # Create copies of the input data to avoid modifying the originals
        current_labeled_texts = labeled_texts.copy()
        current_labeled_labels = labeled_labels.copy()
        remaining_unlabeled = unlabeled_texts.copy()
        
        # Avoid modification of the original lists
        iteration = 0
        while len(remaining_unlabeled) > 0 and iteration < max_iterations:
            iteration += 1
            print(f"\nPseudo-labeling iteration {iteration}/{max_iterations}")
            print(f"Current labeled examples: {len(current_labeled_texts)}")
            print(f"Remaining unlabeled examples: {len(remaining_unlabeled)}")
            
            # Reset and retrain the model on current labeled data
            self.model = self._create_model()
            
            # Encode the current labeled data
            labeled_inputs = self.encode_texts(current_labeled_texts)
            labeled_labels_onehot = to_categorical(current_labeled_labels, num_classes=2)
            
            # Encode validation data
            val_inputs = self.encode_texts(val_texts)
            val_labels_onehot = to_categorical(val_labels, num_classes=2)
            
            # Train the model
            self.model.fit(
                labeled_inputs,
                labeled_labels_onehot,
                validation_data=(val_inputs, val_labels_onehot),
                epochs=epochs_per_iter,
                batch_size=batch_size,
                verbose=1
            )
            
            # Get predictions on unlabeled data
            unlabeled_inputs = self.encode_texts(remaining_unlabeled)
            predictions_prob = self.model.predict(unlabeled_inputs)
            
            # Find confident predictions (max probability across classes)
            confidence_scores = np.max(predictions_prob, axis=1)
            pseudo_labels = np.argmax(predictions_prob, axis=1)
            
            # Find examples with confidence above threshold
            confident_idx = np.where(confidence_scores >= confidence_threshold)[0]
            
            if len(confident_idx) == 0:
                print(f"No confident predictions above threshold {confidence_threshold} in iteration {iteration}.")
                print("Stopping pseudo-labeling process.")
                break
            
            # Add confident examples to the labeled dataset
            for idx in confident_idx:
                current_labeled_texts.append(remaining_unlabeled[idx])
                current_labeled_labels.append(int(pseudo_labels[idx]))
            
            # Remove confident examples from the unlabeled dataset
            # (iterate in reverse to avoid index issues)
            for idx in sorted(confident_idx, reverse=True):
                remaining_unlabeled.pop(idx)
            
            print(f"Added {len(confident_idx)} pseudo-labeled examples.")
        
        print(f"\nPseudo-labeling complete. Final labeled dataset size: {len(current_labeled_texts)}")
        print(f"Original size: {len(labeled_texts)}, Added: {len(current_labeled_texts) - len(labeled_texts)}")
        
        return current_labeled_texts, current_labeled_labels
    
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
            "bert_model_name": self.bert_model_name,
            "max_len": self.max_len,
            "learning_rate": self.learning_rate,
            "dense_units": self.dense_units,
            "freeze_weights": self.freeze_weights,
            "training_time_seconds": self.training_time,
            "epochs_trained": self.epochs_trained,
            "timestamp": datetime.now().isoformat(),
        })
        
        # Save metrics to file
        with open(output_path, 'w') as f:
            json.dump(metrics_dict, f, indent=2)
        
        print(f"Metrics saved to {output_path}")
