"""
Metrics utilities for machine learning model evaluation.

This module provides functions to calculate evaluation metrics for binary classification
models, with standardized naming conventions and formatting.
"""
from typing import Dict, Union, Optional

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    roc_auc_score,
    average_precision_score
)


def evaluate_binary(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    threshold: float = 0.5,
    prefix: Optional[str] = None
) -> Dict[str, float]:
    """
    Calculate binary classification metrics based on probability predictions.
    
    Args:
        y_true (np.ndarray): True binary labels (0 or 1).
        y_prob (np.ndarray): Probability estimates of the positive class.
        threshold (float, optional): Decision threshold for positive class. Defaults to 0.5.
        prefix (Optional[str], optional): Prefix to add to metric keys (e.g., "val_" or "test_").
            Defaults to None (no prefix).
    
    Returns:
        Dict[str, float]: Dictionary containing the following metrics:
            - accuracy: Accuracy score
            - precision: Precision score
            - recall: Recall score
            - f1: F1 score
            - roc_auc: Area under the ROC curve
            - pr_auc: Area under the precision-recall curve
            
    Raises:
        ValueError: If y_true or y_prob contain invalid values or have incompatible shapes.
        
    Example:
        >>> y_true = np.array([0, 1, 1, 0, 1])
        >>> y_prob = np.array([0.1, 0.9, 0.8, 0.3, 0.7])
        >>> evaluate_binary(y_true, y_prob, threshold=0.5, prefix="val_")
        {
            'val_accuracy': 1.0,
            'val_precision': 1.0,
            'val_recall': 1.0,
            'val_f1': 1.0,
            'val_roc_auc': 1.0,
            'val_pr_auc': 1.0
        }
    """
    # Input validation
    y_true = np.asarray(y_true)
    y_prob = np.asarray(y_prob)
    
    if y_true.shape != y_prob.shape:
        raise ValueError(f"y_true and y_prob must have the same shape. "
                         f"Got {y_true.shape} and {y_prob.shape}.")
    
    if not np.all(np.logical_or(y_true == 0, y_true == 1)):
        raise ValueError("y_true must contain only binary labels (0 or 1).")
    
    if np.any(np.logical_or(y_prob < 0, y_prob > 1)):
        raise ValueError("y_prob must contain probabilities between 0 and 1.")
    
    # Apply threshold to get binary predictions
    y_pred = (y_prob >= threshold).astype(int)
    
    # Calculate metrics
    metrics = {
        "accuracy": accuracy_score(y_true, y_pred),
        "precision": precision_score(y_true, y_pred, zero_division=0),
        "recall": recall_score(y_true, y_pred, zero_division=0),
        "f1": f1_score(y_true, y_pred, zero_division=0),
        "roc_auc": roc_auc_score(y_true, y_prob),
        "pr_auc": average_precision_score(y_true, y_prob)
    }
    
    # Add prefix if provided
    if prefix:
        metrics = {f"{prefix}{key}": value for key, value in metrics.items()}
    
    return metrics
