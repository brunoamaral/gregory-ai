"""
Pseudo-labeling utilities for semi-supervised learning.

This module provides functions for pseudo-labeling unlabelled data using a 
self-training loop with a BERT model, and saving the results to CSV files.
It enables using labeled data to progressively label unlabeled data based on
confidence thresholds, helping improve classifier performance when labeled 
data is scarce.
"""
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union, Dict, Any, Tuple, Callable

import numpy as np
import pandas as pd
import tensorflow as tf
from tensorflow.keras.utils import to_categorical
from transformers import PreTrainedTokenizer

from gregory.ml.bert_wrapper import BertTrainer
from gregory.ml import get_trainer


def generate_pseudo_labels(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    unlabelled_df: pd.DataFrame,
    text_column: str = 'text',
    label_column: str = 'relevant',
    confidence: float = 0.9,
    max_iter: int = 7,
    algorithm: str = 'pubmed_bert',
    model_params: Optional[Dict[str, Any]] = None,
    verbose: bool = True
) -> pd.DataFrame:
    """
    Generate pseudo-labels for unlabelled data using self-training.
    
    Args:
        train_df (pd.DataFrame): DataFrame with labelled training data.
        val_df (pd.DataFrame): DataFrame with validation data.
        unlabelled_df (pd.DataFrame): DataFrame with unlabelled data to be pseudo-labelled.
        text_column (str, optional): Name of the text column. Defaults to 'text'.
        label_column (str, optional): Name of the label column. Defaults to 'relevant'.
        confidence (float, optional): Threshold for confident predictions. Defaults to 0.9.
        max_iter (int, optional): Maximum number of iterations. Defaults to 7.
        algorithm (str, optional): Algorithm to use from 'pubmed_bert', 'lgbm_tfidf', 'lstm'.
            Defaults to 'pubmed_bert'.
        model_params (Optional[Dict[str, Any]], optional): Parameters for the trainer.
            Defaults to None.
        verbose (bool, optional): Whether to print progress messages. Defaults to True.
    
    Returns:
        pd.DataFrame: Combined DataFrame with original and pseudo-labelled data.
    
    Note:
        The function uses the specified model trainer with appropriate parameters.
        For BERT, the base model is frozen by default for efficiency.
        Predictions with confidence >= threshold are added to the training set.
        
    Example:
        >>> train_df = pd.DataFrame({
        ...     'article_id': [1, 2],
        ...     'text': ['cancer treatment study', 'economic impact report'],
        ...     'relevant': [1, 0]
        ... })
        >>> val_df = pd.DataFrame({
        ...     'article_id': [3, 4],
        ...     'text': ['oncology research findings', 'market analysis'],
        ...     'relevant': [1, 0]
        ... })
        >>> unlabelled_df = pd.DataFrame({
        ...     'article_id': [5, 6, 7],
        ...     'text': ['tumor suppression mechanism', 'quarterly earnings', 'patient outcomes']
        ... })
        >>> result_df = generate_pseudo_labels(train_df, val_df, unlabelled_df)
    """
    # Ensure we have a copy of the original DataFrames
    train_df = train_df.copy()
    val_df = val_df.copy()
    unlabelled_df = unlabelled_df.copy()
    
    # Set default parameters based on algorithm
    model_params = model_params or {}
    if algorithm == 'pubmed_bert':
        model_params.setdefault('max_len', 400)
        model_params.setdefault('learning_rate', 2e-5)
        model_params.setdefault('dense_units', 48)
        model_params.setdefault('freeze_weights', True)  # Freeze the base model for efficiency
    elif algorithm == 'lgbm_tfidf':
        model_params.setdefault('random_state', 42)
    elif algorithm == 'lstm':
        model_params.setdefault('max_tokens', 10000)
        model_params.setdefault('sequence_length', 100)
    
    if verbose:
        print(f"Starting pseudo-labeling with {len(train_df)} labelled and {len(unlabelled_df)} unlabelled examples")
        print(f"Using algorithm: {algorithm}, confidence threshold: {confidence}, max iterations: {max_iter}")
    
    iteration = 0
    remaining_unlabelled = unlabelled_df.copy()
    
    # Initialize a column to track which rows are pseudo-labelled
    train_df['pseudo_labelled'] = False
    
    while len(remaining_unlabelled) > 0 and iteration < max_iter:
        iteration += 1
        if verbose:
            print(f"\nIteration {iteration}/{max_iter}")
            print(f"Training set size: {len(train_df)}")
            print(f"Remaining unlabelled examples: {len(remaining_unlabelled)}")
        
        # Initialize a new trainer for each iteration using the factory function
        trainer = get_trainer(algorithm, **model_params)
        
        # Prepare training data
        train_texts = train_df[text_column].tolist()
        train_labels = train_df[label_column].tolist()
        
        # Prepare validation data
        val_texts = val_df[text_column].tolist()
        val_labels = val_df[label_column].tolist()
        
        # Train the model (using common interface across all trainers)
        trainer.train(
            train_texts=train_texts,
            train_labels=train_labels,
            val_texts=val_texts,
            val_labels=val_labels,
            epochs=5,  # Fewer epochs for faster iterations
            batch_size=16
        )
        
        # Get predictions on unlabelled data
        unlabelled_texts = remaining_unlabelled[text_column].tolist()
        predictions, probabilities = trainer.predict(
            texts=unlabelled_texts,
            threshold=0.5  # Use a lower threshold for prediction (we'll filter by confidence)
        )
        
        # Get confidence scores (maximum probability across classes)
        # Convert predictions to class probabilities if not already
        if isinstance(probabilities[0], float):
            # Binary classification case - probabilities are for positive class
            confidence_scores = [max(p, 1-p) for p in probabilities]
            class_predictions = [1 if p >= 0.5 else 0 for p in probabilities]
        else:
            # Multi-class case - probabilities are arrays
            confidence_scores = [np.max(p) for p in probabilities]
            class_predictions = [np.argmax(p) for p in probabilities]
        
        # Identify confident predictions
        confident_indices = [i for i, score in enumerate(confidence_scores) if score >= confidence]
        
        if not confident_indices:
            if verbose:
                print(f"No confident predictions above threshold {confidence} in iteration {iteration}.")
                print("Stopping pseudo-labeling process.")
            break
        
        # Extract confident examples
        confident_rows = remaining_unlabelled.iloc[confident_indices].copy()
        confident_rows[label_column] = [class_predictions[i] for i in confident_indices]
        confident_rows['confidence'] = [confidence_scores[i] for i in confident_indices]
        confident_rows['pseudo_labelled'] = True
        confident_rows['pseudo_iteration'] = iteration
        
        # Add confident examples to training set
        train_df = pd.concat([train_df, confident_rows], ignore_index=True)
        
        # Remove confident examples from unlabelled set
        remaining_indices = [i for i in range(len(remaining_unlabelled)) if i not in confident_indices]
        remaining_unlabelled = remaining_unlabelled.iloc[remaining_indices].reset_index(drop=True)
        
        if verbose:
            print(f"Added {len(confident_indices)} pseudo-labelled examples with confidence >= {confidence}")
    
    if verbose:
        print(f"\nPseudo-labeling complete after {iteration} iterations")
        print(f"Final training set: {len(train_df)} examples "
              f"({len(train_df[train_df['pseudo_labelled']])} pseudo-labelled)")
    
    # Add metadata columns to help track the original and pseudo-labelled data
    train_df['pseudo_confidence'] = train_df.apply(
        lambda row: row['confidence'] if row['pseudo_labelled'] else None, axis=1
    )
    
    return train_df


def get_pseudo_label_stats(df: pd.DataFrame, label_column: str = 'relevant') -> Dict[str, Any]:
    """
    Generate statistics about the pseudo-labeled dataset.
    
    Args:
        df (pd.DataFrame): DataFrame with pseudo-labeled data.
        label_column (str, optional): Name of the label column. Defaults to 'relevant'.
    
    Returns:
        Dict[str, Any]: Dictionary of statistics including:
            - total_examples: Total number of examples
            - original_examples: Number of original labeled examples
            - pseudo_examples: Number of pseudo-labeled examples
            - pseudo_examples_per_class: Dictionary with count per class
            - pseudo_examples_per_iteration: Dictionary with count per iteration
            - average_confidence: Average confidence of pseudo-labels
            - class_distribution: Distribution of classes in the final dataset
            
    Example:
        >>> df = generate_pseudo_labels(train_df, val_df, unlabelled_df)
        >>> stats = get_pseudo_label_stats(df)
        >>> print(f"Added {stats['pseudo_examples']} pseudo-labels")
        Added 120 pseudo-labels
    """
    stats = {
        'total_examples': len(df),
        'original_examples': len(df[~df['pseudo_labelled']]),
        'pseudo_examples': len(df[df['pseudo_labelled']]),
    }
    
    # Class distribution for pseudo-labeled examples
    pseudo_df = df[df['pseudo_labelled']]
    if not pseudo_df.empty:
        class_counts = pseudo_df[label_column].value_counts().to_dict()
        stats['pseudo_examples_per_class'] = class_counts
        
        # Iterations stats
        if 'pseudo_iteration' in pseudo_df.columns:
            iteration_counts = pseudo_df['pseudo_iteration'].value_counts().sort_index().to_dict()
            stats['pseudo_examples_per_iteration'] = iteration_counts
        
        # Confidence stats
        if 'confidence' in pseudo_df.columns:
            stats['average_confidence'] = pseudo_df['confidence'].mean()
            stats['min_confidence'] = pseudo_df['confidence'].min()
            stats['max_confidence'] = pseudo_df['confidence'].max()
    
    # Overall class distribution
    overall_class_counts = df[label_column].value_counts().to_dict()
    stats['class_distribution'] = overall_class_counts
    
    return stats


def save_pseudo_csv(
    df: pd.DataFrame, 
    dest_dir: Union[str, Path],
    prefix: str = '',
    include_timestamp: bool = True,
    verbose: bool = True
) -> Path:
    """
    Save a DataFrame with pseudo-labels to a CSV file with a timestamped filename.
    
    Args:
        df (pd.DataFrame): DataFrame to save.
        dest_dir (Union[str, Path]): Directory to save the CSV file in.
        prefix (str, optional): Prefix to add to the filename. Defaults to ''.
        include_timestamp (bool, optional): Whether to include timestamp in filename. 
            Defaults to True.
        verbose (bool, optional): Whether to print save information. Defaults to True.
    
    Returns:
        Path: Path to the saved CSV file.
    
    Note:
        Creates filename in the format: prefix_YYYYMMDD_HHMMSS.csv
        If the file already exists, adds a suffix _2, _3, etc.
        
    Example:
        >>> df = pd.DataFrame({'text': ['example'], 'relevant': [1], 'pseudo_labelled': [True]})
        >>> path = save_pseudo_csv(df, '/tmp/pseudo_labels', prefix='bert')
        >>> str(path)
        '/tmp/pseudo_labels/bert_20250518_123456.csv'
    """
    # Ensure dest_dir exists
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate filename components
    components = []
    if prefix:
        components.append(prefix)
    
    if include_timestamp:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        components.append(timestamp)
    elif not components:
        # If no prefix and no timestamp, use 'pseudo_labels' as default
        components.append('pseudo_labels')
    
    base_filename = f"{'_'.join(components)}.csv"
    filepath = dest_dir / base_filename
    
    # Handle name collisions
    suffix = 1
    while filepath.exists():
        suffix += 1
        new_filename = f"{'_'.join(components)}_{suffix}.csv"
        filepath = dest_dir / new_filename
    
    # Save the DataFrame
    df.to_csv(filepath, index=False)
    
    if verbose:
        print(f"Saved pseudo-labels to {filepath}")
    
    return filepath


def load_and_filter_pseudo_labels(
    filepath: Union[str, Path],
    min_confidence: Optional[float] = None,
    max_iteration: Optional[int] = None,
    include_original: bool = True,
    label_column: str = 'relevant'
) -> pd.DataFrame:
    """
    Load a pseudo-labeled dataset and filter it based on confidence and iteration.
    
    Args:
        filepath (Union[str, Path]): Path to the CSV file with pseudo-labeled data.
        min_confidence (Optional[float], optional): Minimum confidence threshold. 
            Defaults to None (no filtering).
        max_iteration (Optional[int], optional): Maximum iteration to include. 
            Defaults to None (all iterations).
        include_original (bool, optional): Whether to include original labeled data. 
            Defaults to True.
        label_column (str, optional): Name of the label column. Defaults to 'relevant'.
    
    Returns:
        pd.DataFrame: Filtered DataFrame.
    
    Example:
        >>> path = save_pseudo_csv(pseudo_df, '/tmp/pseudo')
        >>> filtered_df = load_and_filter_pseudo_labels(path, min_confidence=0.95)
    """
    # Load the dataset
    df = pd.read_csv(filepath)
    
    if 'pseudo_labelled' not in df.columns:
        raise ValueError("The dataset does not contain pseudo-labeling information.")
    
    # Start with original examples if requested, otherwise an empty DataFrame
    if include_original:
        result_df = df[~df['pseudo_labelled']].copy()
    else:
        result_df = df[[]].copy()  # Empty DataFrame with same columns
    
    # Filter pseudo-labeled examples
    pseudo_df = df[df['pseudo_labelled']].copy()
    
    # Apply confidence filter if specified
    if min_confidence is not None and 'confidence' in pseudo_df.columns:
        pseudo_df = pseudo_df[pseudo_df['confidence'] >= min_confidence]
    
    # Apply iteration filter if specified
    if max_iteration is not None and 'pseudo_iteration' in pseudo_df.columns:
        pseudo_df = pseudo_df[pseudo_df['pseudo_iteration'] <= max_iteration]
    
    # Combine original (if included) and filtered pseudo-labeled examples
    result_df = pd.concat([result_df, pseudo_df], ignore_index=True)
    
    return result_df
