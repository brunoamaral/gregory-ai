"""
LightGBM with TF-IDF implementation for text classification.

This module provides the LGBMTfidfTrainer class for training, evaluating, and saving
LightGBM models with TF-IDF vectorization for text classification.
"""
import json
import time
import joblib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union, Any

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
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
import lightgbm as lgbm


class LGBMTfidfTrainer:
    """
    Trainer for LightGBM text classification models with TF-IDF vectorization.
    
    This class handles the creation, training, evaluation, and saving of LightGBM-based
    text classification models using TF-IDF vectorization.
    
    Attributes:
        tfidf_params (dict): Parameters for the TF-IDF vectorizer
        lgbm_params (dict): Parameters for the LightGBM model
        random_state (int): Random state for reproducibility
        vectorizer (TfidfVectorizer): The TF-IDF vectorizer
        model (lgbm.LGBMClassifier): The LightGBM classifier
        training_time (float): Time taken for training in seconds
        n_rounds (int): Number of boosting rounds used in training
    """
    
    def __init__(
        self,
        tfidf_params: Optional[Dict[str, Any]] = None,
        lgbm_params: Optional[Dict[str, Any]] = None,
        random_state: int = 69
    ):
        """
        Initialize a new LGBMTfidfTrainer.
        
        Args:
            tfidf_params (Optional[Dict[str, Any]], optional): Parameters for TfidfVectorizer.
                Defaults to None, which will use {'max_features': 10000, 'min_df': 2, 'max_df': 0.95}.
            lgbm_params (Optional[Dict[str, Any]], optional): Parameters for LGBMClassifier.
                Defaults to None, which will use LightGBM default parameters plus random_state.
            random_state (int, optional): Random seed. Defaults to 69.
        """
        # Default parameters for TF-IDF
        self.tfidf_params = tfidf_params or {
            'max_features': 10000,
            'min_df': 2,
            'max_df': 0.95,
            'ngram_range': (1, 2)
        }
        
        # Default parameters for LightGBM
        self.lgbm_params = lgbm_params or {
            'objective': 'binary',
            'metric': 'binary_logloss',
            'boosting_type': 'gbdt',
            'num_leaves': 31,
            'learning_rate': 0.05,
            'feature_fraction': 0.9,
            'bagging_fraction': 0.8,
            'bagging_freq': 5,
            'verbose': -1
        }
        
        self.random_state = random_state
        self.lgbm_params['random_state'] = random_state
        
        # Initialize vectorizer and model
        self.vectorizer = TfidfVectorizer(**self.tfidf_params)
        self.model = lgbm.LGBMClassifier(**self.lgbm_params)
        
        # Training attributes
        self.training_time = None
        self.n_rounds = 100  # Default number of boosting rounds
    
    def train(
        self, 
        train_texts: List[str], 
        train_labels: List[int],
        val_texts: List[str], 
        val_labels: List[int],
        num_boost_round: int = 100,
        early_stopping_rounds: int = 10,
        verbose_eval: bool = True
    ) -> Dict[str, List[float]]:
        """
        Train the LightGBM model with TF-IDF features.
        
        Args:
            train_texts (List[str]): Training text data
            train_labels (List[int]): Training labels (0/1)
            val_texts (List[str]): Validation text data
            val_labels (List[int]): Validation labels (0/1)
            num_boost_round (int, optional): Number of boosting rounds. Defaults to 100.
            early_stopping_rounds (int, optional): Early stopping patience. Defaults to 10.
            verbose_eval (bool, optional): Whether to print training progress. Defaults to True.
            
        Returns:
            Dict[str, List[float]]: Training history with metrics
        """
        # Record start time
        start_time = time.time()
        
        # Fit vectorizer on training data
        print("Fitting TF-IDF vectorizer...")
        X_train = self.vectorizer.fit_transform(train_texts)
        
        # Transform validation data
        print("Transforming validation data...")
        X_val = self.vectorizer.transform(val_texts)
        
        # Prepare evaluation set for LightGBM
        eval_set = [(X_train, train_labels), (X_val, val_labels)]
        eval_names = ['train', 'valid']
        
        # Set the rounds
        self.n_rounds = num_boost_round
        
        # Train the model
        print(f"Training LightGBM model with {self.n_rounds} boosting rounds...")
        self.model.fit(
            X_train, 
            train_labels,
            eval_set=eval_set,
            eval_names=eval_names,
            eval_metric=['binary_logloss', 'auc'],
            early_stopping_rounds=early_stopping_rounds,
            verbose=10 if verbose_eval else 0,
        )
        
        # Record training time
        self.training_time = time.time() - start_time
        
        # Get the training history
        history = {
            'iterations': list(range(len(self.model.evals_result_['valid']['binary_logloss']))),
            'train_loss': self.model.evals_result_['train']['binary_logloss'],
            'val_loss': self.model.evals_result_['valid']['binary_logloss'],
            'train_auc': self.model.evals_result_['train']['auc'],
            'val_auc': self.model.evals_result_['valid']['auc']
        }
        
        print(f"Training completed in {self.training_time:.2f} seconds.")
        print(f"Best iteration: {self.model.best_iteration_}")
        
        return history
    
    def evaluate(
        self, 
        test_texts: List[str], 
        test_labels: List[int],
        threshold: float = 0.8
    ) -> Dict[str, Union[float, Dict]]:
        """
        Evaluate the LightGBM model on test data.
        
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
        # Transform test data
        X_test = self.vectorizer.transform(test_texts)
        
        # Get predictions
        predictions_prob = self.model.predict_proba(X_test)[:, 1]
        
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
        
        # Transform texts
        X = self.vectorizer.transform(texts)
        
        # Get predictions
        probabilities = self.model.predict_proba(X)[:, 1].tolist()
        
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
        
        # Save model and vectorizer
        model_path = model_dir / "lgbm_classifier.joblib"
        vectorizer_path = model_dir / "tfidf_vectorizer.joblib"
        
        joblib.dump(self.model, model_path)
        joblib.dump(self.vectorizer, vectorizer_path)
        
        # Save metrics and parameters
        metrics_path = model_dir / "metrics.json"
        
        metrics_info = {
            "tfidf_params": self.tfidf_params,
            "lgbm_params": {k: v for k, v in self.lgbm_params.items() if not callable(v)},
            "training_time_seconds": self.training_time,
            "n_rounds": self.n_rounds,
            "best_iteration": self.model.best_iteration_,
            "feature_importances": self.model.feature_importances_.tolist() if hasattr(self.model, 'feature_importances_') else [],
            "timestamp": datetime.now().isoformat(),
        }
        
        with open(metrics_path, 'w') as f:
            json.dump(metrics_info, f, indent=2)
        
        return {
            "model_path": str(model_path),
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
        
        model_path = model_dir / "lgbm_classifier.joblib"
        vectorizer_path = model_dir / "tfidf_vectorizer.joblib"
        
        if not model_path.exists() or not vectorizer_path.exists():
            raise FileNotFoundError(f"Model files not found in {model_dir}")
        
        self.model = joblib.load(model_path)
        self.vectorizer = joblib.load(vectorizer_path)
        
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
            "tfidf_params": self.tfidf_params,
            "lgbm_params": {k: v for k, v in self.lgbm_params.items() if not callable(v)},
            "training_time_seconds": self.training_time,
            "n_rounds": self.n_rounds,
            "timestamp": datetime.now().isoformat(),
        })
        
        # Save metrics to file
        with open(output_path, 'w') as f:
            json.dump(metrics_dict, f, indent=2)
        
        print(f"Metrics saved to {output_path}")
