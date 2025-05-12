"""
LightGBM with TF-IDF model utility functions for text classification.

This module provides a wrapper class for using LightGBM models with TF-IDF features
in the Django application, with functionality for training, evaluation, and prediction.
"""

import os
import time
import numpy as np
import pandas as pd
import json
import joblib
import lightgbm as lgb
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import accuracy_score, recall_score, precision_score, f1_score, roc_auc_score
import logging

logger = logging.getLogger(__name__)

class LGBMTfidfClassifier:
    """
    A class used to represent a LightGBM classifier with TF-IDF vectorization for text classification.
    """

    def __init__(self, lgbm_params=None, random_seed=42):
        """
        Initialize the LightGBM classifier with TF-IDF vectorization.
        
        Args:
            lgbm_params (dict): Parameters for LightGBM classifier
            random_seed (int): Random seed for reproducibility
        """
        # Default parameters based on notebook
        if lgbm_params is None:
            lgbm_params = {
                'colsample_bytree': 0.5527441519925319,
                'learning_rate': 0.05670501625777839,
                'max_depth': 13,
                'min_child_samples': 6,
                'n_estimators': 231,
                'num_leaves': 19,
                'reg_alpha': 0.028846140312283053,
                'reg_lambda': 0.5494358889794788,
                'subsample': 0.8310616529434425
            }
        
        self.vectorizer = TfidfVectorizer()
        self.classifier = lgb.LGBMClassifier(**lgbm_params, random_state=random_seed)
        self.fitted = False
        self.metadata = {
            'model_type': 'LGBM_TFIDF',
            'random_seed': random_seed,
            'lgbm_params': lgbm_params,
            'training_history': {},
            'metrics': {},
            'created_at': None,
            'training_time_seconds': None,
            'vectorizer_params': {}
        }

    def train(self, X_train, y_train, vectorizer_params=None):
        """
        Train the LightGBM classifier with TF-IDF vectorization.
        
        Args:
            X_train (pd.Series or list): Training text data
            y_train (pd.Series or list): Training labels
            vectorizer_params (dict): Parameters for TF-IDF vectorizer
            
        Returns:
            self: Trained classifier instance
        """
        start_time = time.time()
        
        # Update vectorizer with custom parameters if provided
        if vectorizer_params:
            self.vectorizer = TfidfVectorizer(**vectorizer_params)
            self.metadata['vectorizer_params'] = vectorizer_params
        
        # Transform the text data with TF-IDF
        X_train_transformed = self.vectorizer.fit_transform(X_train)
        
        # Train the classifier
        self.classifier.fit(X_train_transformed, y_train)
        self.fitted = True
        
        # Update metadata
        training_time = time.time() - start_time
        self.metadata['created_at'] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.metadata['training_time_seconds'] = training_time
        self.metadata['training_history'] = {
            'n_samples': X_train_transformed.shape[0],
            'n_features': X_train_transformed.shape[1],
            'class_distribution': dict(pd.Series(y_train).value_counts().to_dict())
        }
        
        logger.info(f"LGBM-TFIDF model training completed in {training_time:.2f} seconds")
        
        return self

    def predict(self, texts):
        """
        Make binary predictions using the trained model.
        
        Args:
            texts (list or pd.Series): List of text strings
            
        Returns:
            np.ndarray: Binary predictions (0 or 1)
        """
        if not self.fitted:
            raise ValueError("Model not trained or loaded. Train a model or load a pre-trained model first.")
        
        X_transformed = self.vectorizer.transform(texts)
        return self.classifier.predict(X_transformed)

    def predict_proba(self, texts):
        """
        Make probability predictions using the trained model.
        
        Args:
            texts (list or pd.Series): List of text strings
            
        Returns:
            np.ndarray: Prediction probabilities
        """
        if not self.fitted:
            raise ValueError("Model not trained or loaded. Train a model or load a pre-trained model first.")
        
        X_transformed = self.vectorizer.transform(texts)
        return self.classifier.predict_proba(X_transformed)

    def evaluate(self, X_test, y_test):
        """
        Evaluate the model on test data.
        
        Args:
            X_test (list or pd.Series): Test text data
            y_test (list or pd.Series): Test labels
            
        Returns:
            dict: Evaluation metrics
        """
        if not self.fitted:
            raise ValueError("Model not trained or loaded. Train a model or load a pre-trained model first.")
        
        X_test_transformed = self.vectorizer.transform(X_test)
        y_pred = self.classifier.predict(X_test_transformed)
        y_prob = self.classifier.predict_proba(X_test_transformed)[:, 1] if len(self.classifier.classes_) == 2 else None
        
        # Calculate metrics
        metrics = {
            'accuracy': accuracy_score(y_test, y_pred),
            'precision': precision_score(y_test, y_pred, average='binary', zero_division=0),
            'recall': recall_score(y_test, y_pred, average='binary', zero_division=0),
            'f1_score': f1_score(y_test, y_pred, average='binary', zero_division=0)
        }
        
        # Add AUC-ROC score if applicable (binary classification)
        if y_prob is not None:
            metrics['auc_roc'] = roc_auc_score(y_test, y_prob)
        
        # Update metadata
        self.metadata['metrics'] = metrics
        
        return metrics

    def save_model(self, model_dir):
        """
        Save the model and metadata to disk.
        
        Args:
            model_dir (str): Directory to save the model
        """
        if not self.fitted:
            raise ValueError("No trained model to save. Train a model first.")
        
        os.makedirs(model_dir, exist_ok=True)
        
        # Save the classifier
        classifier_path = os.path.join(model_dir, 'lgbm_classifier.joblib')
        joblib.dump(self.classifier, classifier_path)
        
        # Save the vectorizer
        vectorizer_path = os.path.join(model_dir, 'tfidf_vectorizer.joblib')
        joblib.dump(self.vectorizer, vectorizer_path)
        
        # Save metadata
        metadata_path = os.path.join(model_dir, 'metadata.json')
        with open(metadata_path, 'w') as f:
            json.dump(self.metadata, f, indent=2)
        
        logger.info(f"Model saved to {model_dir}")
        
        return {
            'classifier_path': classifier_path,
            'vectorizer_path': vectorizer_path,
            'metadata_path': metadata_path
        }

    def load_model(self, classifier_path, vectorizer_path, metadata_path=None):
        """
        Load a pre-trained model from disk.
        
        Args:
            classifier_path (str): Path to the saved classifier
            vectorizer_path (str): Path to the saved vectorizer
            metadata_path (str): Path to the saved metadata
            
        Returns:
            self: Loaded classifier instance
        """
        # Load the classifier
        self.classifier = joblib.load(classifier_path)
        
        # Load the vectorizer
        self.vectorizer = joblib.load(vectorizer_path)
        
        # Load metadata if available
        if metadata_path and os.path.exists(metadata_path):
            with open(metadata_path, 'r') as f:
                self.metadata = json.load(f)
        
        self.fitted = True
        
        logger.info(f"Model loaded: vectorizer from {vectorizer_path}, classifier from {classifier_path}")
        
        return self
