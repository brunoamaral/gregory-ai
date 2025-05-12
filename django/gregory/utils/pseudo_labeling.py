"""
Utility functions for pseudo-labeling in the training process.

This module provides functions to apply pseudo-labeling techniques 
to unlabeled data during model training. It includes:
- Loading and filtering unlabeled data based on confidence thresholds
- Capping pseudo-labels per class to match hand-labeled examples
- Selecting highest-confidence examples when capping
"""

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


def apply_pseudo_labeling(unlabeled_df, model, vectorizer, labeled_train_df, confidence_threshold=0.9):
    """
    Apply pseudo-labeling to unlabeled data using the provided model.
    
    Args:
        unlabeled_df (pd.DataFrame): DataFrame with unlabeled articles
        model: Trained model to use for predictions
        vectorizer: Vectorizer to transform text data
        labeled_train_df (pd.DataFrame): DataFrame with labeled training data
        confidence_threshold (float): Confidence threshold (default: 0.9)
        
    Returns:
        pd.DataFrame: DataFrame with pseudo-labeled articles that meet the threshold
    """
    if unlabeled_df.empty:
        logger.info("No unlabeled data available for pseudo-labeling")
        return pd.DataFrame()
    
    # Vectorize the unlabeled data
    X_unlabeled = vectorizer.transform(unlabeled_df['cleaned_text'])
    
    # Get predictions and probabilities
    try:
        # Try to get probability scores (works with sklearn models)
        y_probs = model.predict_proba(X_unlabeled)
        # Get the maximum probability for each prediction (confidence score)
        confidences = np.max(y_probs, axis=1)
        predictions = np.argmax(y_probs, axis=1)
    except AttributeError:
        # Fallback if model doesn't have predict_proba
        logger.warning("Model doesn't support predict_proba, using binary predictions only")
        predictions = model.predict(X_unlabeled)
        # Without probabilities, we can't apply a confidence threshold
        return pd.DataFrame()
    
    # Create DataFrame with predictions and confidence scores
    pseudo_df = unlabeled_df.copy()
    pseudo_df['is_relevant'] = predictions
    pseudo_df['confidence'] = confidences
    
    # Apply threshold to both positive and negative predictions
    high_confidence_df = pseudo_df[
        ((pseudo_df['is_relevant'] == 1) & (pseudo_df['confidence'] >= confidence_threshold)) | 
        ((pseudo_df['is_relevant'] == 0) & (pseudo_df['confidence'] >= confidence_threshold))
    ]
    
    logger.info(f"Applied pseudo-labeling to {len(unlabeled_df)} unlabeled samples, "
                f"found {len(high_confidence_df)} with confidence >= {confidence_threshold}")
    
    return high_confidence_df


def cap_pseudo_labels(pseudo_labeled_df, labeled_df):
    """
    Cap the number of pseudo-labeled examples per class to match the hand-labeled examples.
    
    Args:
        pseudo_labeled_df (pd.DataFrame): DataFrame with pseudo-labeled articles
        labeled_df (pd.DataFrame): DataFrame with hand-labeled articles
        
    Returns:
        pd.DataFrame: DataFrame with capped pseudo-labeled articles
    """
    if pseudo_labeled_df.empty:
        return pd.DataFrame()
    
    # Count the number of examples per class in the labeled data
    class_counts = labeled_df['is_relevant'].value_counts()
    
    # Initialize the result DataFrame
    capped_pseudo_df = pd.DataFrame()
    
    # Process each class separately
    for class_label, count in class_counts.items():
        # Get pseudo-labeled examples for this class
        class_pseudo = pseudo_labeled_df[pseudo_labeled_df['is_relevant'] == class_label]
        
        if len(class_pseudo) <= count:
            # If we have fewer pseudo-labels than the cap, use all of them
            selected_pseudo = class_pseudo
        else:
            # Otherwise, select the highest confidence examples up to the cap
            selected_pseudo = class_pseudo.nlargest(count, 'confidence')
        
        # Add the selected examples to the result
        capped_pseudo_df = pd.concat([capped_pseudo_df, selected_pseudo])
    
    logger.info(f"Capped {len(pseudo_labeled_df)} pseudo-labeled samples to {len(capped_pseudo_df)} "
                f"to match class distribution in labeled data")
    
    return capped_pseudo_df


def get_pseudo_labeled_data(unlabeled_df, model, vectorizer, labeled_train_df, confidence_threshold=0.9):
    """
    Main function to get pseudo-labeled data with thresholding and capping.
    
    Args:
        unlabeled_df (pd.DataFrame): DataFrame with unlabeled articles
        model: Trained model to use for predictions
        vectorizer: Vectorizer to transform text data
        labeled_train_df (pd.DataFrame): DataFrame with labeled training data
        confidence_threshold (float): Confidence threshold (default: 0.9)
        
    Returns:
        pd.DataFrame: DataFrame with pseudo-labeled articles that meet criteria
    """
    # Get high-confidence pseudo-labels
    pseudo_labeled_df = apply_pseudo_labeling(
        unlabeled_df, model, vectorizer, labeled_train_df, confidence_threshold
    )
    
    if pseudo_labeled_df.empty:
        return pd.DataFrame()
    
    # Cap the number of pseudo-labels per class
    capped_pseudo_df = cap_pseudo_labels(pseudo_labeled_df, labeled_train_df)
    
    # Drop the confidence column as it's not needed anymore
    if 'confidence' in capped_pseudo_df.columns:
        capped_pseudo_df = capped_pseudo_df.drop('confidence', axis=1)
    
    return capped_pseudo_df
