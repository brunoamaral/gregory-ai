"""
BERT model utility functions for text classification.

This module provides a wrapper class for using BERT models in the Django application,
with functionality for training, evaluation, and prediction.
"""

import os
import time
import numpy as np
import pandas as pd
import json
import tensorflow as tf
from tensorflow.keras.layers import Input, Dense, Dropout
from tensorflow.keras.models import Model
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.callbacks import EarlyStopping
from transformers import BertTokenizer, TFBertModel
import logging

logger = logging.getLogger(__name__)

class BERTClassifier:
    """
    A class used to represent a BERT model for text classification.
    """

    def __init__(self, max_len=128, model_name='bert-base-uncased'):
        """
        Initialize the BERT classifier.
        
        Args:
            max_len (int): Maximum length of input sequences
            model_name (str): Name of the BERT model to use
        """
        self.max_len = max_len
        self.model_name = model_name
        self.tokenizer = BertTokenizer.from_pretrained(model_name)
        self.bert_model = TFBertModel.from_pretrained(model_name)
        self.model = None
        self.history = None
        self.metadata = {
            'model_type': 'BERT',
            'max_len': max_len,
            'base_model': model_name,
            'training_history': {},
            'metrics': {},
            'created_at': None,
            'training_time_seconds': None
        }

    def build_model(self, dense_units=64, dropout_rate=0.2, learning_rate=2e-5):
        """
        Build the BERT model architecture.
        
        Args:
            dense_units (int): Number of units in the dense layer
            dropout_rate (float): Dropout rate for regularization
            learning_rate (float): Learning rate for the optimizer
            
        Returns:
            tf.keras.Model: Compiled BERT model
        """
        # Input layers
        input_ids = Input(shape=(self.max_len,), dtype=tf.int32, name="input_ids")
        attention_masks = Input(shape=(self.max_len,), dtype=tf.int32, name="attention_masks")

        # BERT layer
        bert_output = self.bert_model(input_ids, attention_mask=attention_masks)[1]  # Use pooled output

        # Classification layers
        x = Dense(dense_units, activation='relu')(bert_output)
        x = Dropout(dropout_rate)(x)
        output = Dense(1, activation='sigmoid')(x)

        # Model compilation
        model = Model(inputs=[input_ids, attention_masks], outputs=output)
        model.compile(
            optimizer=Adam(learning_rate=learning_rate),
            loss='binary_crossentropy',
            metrics=['accuracy']
        )
        
        return model

    def encode_texts(self, texts):
        """
        Encode a list of texts using the BERT tokenizer.
        
        Args:
            texts (list): List of text strings
            
        Returns:
            tuple: (input_ids, attention_masks) tensors
        """
        input_ids = []
        attention_masks = []

        for text in texts:
            encoded = self.tokenizer.encode_plus(
                text,
                add_special_tokens=True,  # Add [CLS] and [SEP]
                max_length=self.max_len,
                padding='max_length',
                truncation=True,
                return_attention_mask=True,
                return_tensors='tf'
            )
            
            input_ids.append(encoded['input_ids'])
            attention_masks.append(encoded['attention_mask'])
        
        # Convert to tensors
        input_ids = tf.concat(input_ids, axis=0)
        attention_masks = tf.concat(attention_masks, axis=0)

        return input_ids, attention_masks

    def train(self, X_train, y_train, X_val=None, y_val=None, 
              dense_units=64, dropout_rate=0.2, learning_rate=2e-5, 
              batch_size=16, epochs=4, patience=2):
        """
        Train the BERT model.
        
        Args:
            X_train (pd.Series or list): Training text data
            y_train (pd.Series or list): Training labels
            X_val (pd.Series or list): Validation text data
            y_val (pd.Series or list): Validation labels
            dense_units (int): Number of units in the dense layer
            dropout_rate (float): Dropout rate for regularization
            learning_rate (float): Learning rate for the optimizer
            batch_size (int): Batch size for training
            epochs (int): Number of training epochs
            patience (int): Early stopping patience
            
        Returns:
            History object from model training
        """
        start_time = time.time()
        
        # Build the model
        self.model = self.build_model(
            dense_units=dense_units, 
            dropout_rate=dropout_rate, 
            learning_rate=learning_rate
        )
        
        # Encode training data
        X_train_ids, X_train_masks = self.encode_texts(X_train)
        
        # Prepare validation data if provided
        validation_data = None
        if X_val is not None and y_val is not None:
            X_val_ids, X_val_masks = self.encode_texts(X_val)
            validation_data = ([X_val_ids, X_val_masks], y_val)
        
        # Set up callbacks
        callbacks = [EarlyStopping(monitor='val_loss' if validation_data else 'loss', 
                                  patience=patience, restore_best_weights=True)]
        
        # Train the model
        self.history = self.model.fit(
            [X_train_ids, X_train_masks], 
            y_train,
            batch_size=batch_size,
            epochs=epochs,
            validation_data=validation_data,
            callbacks=callbacks,
            verbose=1
        )
        
        # Update metadata
        training_time = time.time() - start_time
        self.metadata['created_at'] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.metadata['training_time_seconds'] = training_time
        self.metadata['training_history'] = {
            'epochs_trained': len(self.history.history['loss']),
            'final_loss': float(self.history.history['loss'][-1]),
            'final_accuracy': float(self.history.history['accuracy'][-1])
        }
        if validation_data:
            self.metadata['training_history']['final_val_loss'] = float(self.history.history['val_loss'][-1])
            self.metadata['training_history']['final_val_accuracy'] = float(self.history.history['val_accuracy'][-1])
        
        self.metadata['model_parameters'] = {
            'dense_units': dense_units,
            'dropout_rate': dropout_rate,
            'learning_rate': learning_rate,
            'batch_size': batch_size,
            'epochs': epochs,
            'patience': patience
        }
        
        logger.info(f"BERT model training completed in {training_time:.2f} seconds")
        
        return self.history

    def predict(self, texts):
        """
        Make predictions using the trained BERT model.
        
        Args:
            texts (list or pd.Series): List of text strings
            
        Returns:
            np.ndarray: Predicted probabilities
        """
        if self.model is None:
            raise ValueError("Model not trained or loaded. Train a model or load a pre-trained model first.")
        
        input_ids, attention_masks = self.encode_texts(texts)
        predictions = self.model.predict([input_ids, attention_masks])
        
        return predictions.flatten()

    def evaluate(self, X_test, y_test):
        """
        Evaluate the model on test data.
        
        Args:
            X_test (list or pd.Series): Test text data
            y_test (list or pd.Series): Test labels
            
        Returns:
            dict: Evaluation metrics
        """
        if self.model is None:
            raise ValueError("Model not trained or loaded. Train a model or load a pre-trained model first.")
        
        input_ids, attention_masks = self.encode_texts(X_test)
        results = self.model.evaluate([input_ids, attention_masks], y_test, verbose=0)
        metrics = {
            'loss': float(results[0]),
            'accuracy': float(results[1])
        }
        
        # Update metadata
        self.metadata['metrics'] = metrics
        
        return metrics

    def save_model(self, model_dir):
        """
        Save the model and metadata to disk.
        
        Args:
            model_dir (str): Directory to save the model
        """
        if self.model is None:
            raise ValueError("No model to save. Train a model first.")
        
        os.makedirs(model_dir, exist_ok=True)
        
        # Save the model
        model_path = os.path.join(model_dir, 'bert_model')
        self.model.save(model_path)
        
        # Save the tokenizer
        tokenizer_path = os.path.join(model_dir, 'tokenizer')
        self.tokenizer.save_pretrained(tokenizer_path)
        
        # Save metadata
        metadata_path = os.path.join(model_dir, 'metadata.json')
        with open(metadata_path, 'w') as f:
            json.dump(self.metadata, f, indent=2)
        
        logger.info(f"Model saved to {model_dir}")

    def load_model(self, model_dir):
        """
        Load a pre-trained model from disk.
        
        Args:
            model_dir (str): Directory containing the saved model
        """
        # Load the model
        model_path = os.path.join(model_dir, 'bert_model')
        self.model = tf.keras.models.load_model(model_path)
        
        # Load the tokenizer if available
        tokenizer_path = os.path.join(model_dir, 'tokenizer')
        if os.path.exists(tokenizer_path):
            self.tokenizer = BertTokenizer.from_pretrained(tokenizer_path)
        
        # Load metadata if available
        metadata_path = os.path.join(model_dir, 'metadata.json')
        if os.path.exists(metadata_path):
            with open(metadata_path, 'r') as f:
                self.metadata = json.load(f)
                self.max_len = self.metadata.get('max_len', self.max_len)
        
        logger.info(f"Model loaded from {model_dir}")
