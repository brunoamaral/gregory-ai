"""
Dataset utilities for ML models.

This module provides functions to collect article data, build datasets,
and split data for training, validation, and testing of ML models.
"""
from datetime import datetime, timedelta
from typing import Optional, Tuple, Union

import pandas as pd
from django.db.models import Q, QuerySet
from sklearn.model_selection import train_test_split

from gregory.models import Articles, Team, Subject, ArticleSubjectRelevance


def collect_articles(team_slug: str, subject_slug: str, window_days: Optional[int] = None) -> QuerySet:
    """
    Collect articles for a specific team and subject, optionally within a time window.
    
    Args:
        team_slug (str): The slug identifier for the team
        subject_slug (str): The slug identifier for the subject
        window_days (Optional[int], optional): If provided, only articles discovered 
            within this many days will be included. Defaults to None.
    
    Returns:
        QuerySet: A queryset of Articles objects
        
    Raises:
        Team.DoesNotExist: If team with the provided slug doesn't exist
        Subject.DoesNotExist: If subject with the provided slug doesn't exist
    """
    # Get team and subject
    team = Team.objects.get(slug=team_slug)
    subject = Subject.objects.get(subject_slug=subject_slug, team=team)
    
    # Base queryset filtering for team and subject
    queryset = Articles.objects.filter(teams=team, subjects=subject)
    
    # Add time window filter if specified
    if window_days is not None:
        cutoff_date = datetime.now() - timedelta(days=window_days)
        queryset = queryset.filter(discovery_date__gte=cutoff_date)
    
    # After window filter, require at least one relevance entry
    # TODO: check if this impacts pseudo labelling 
    queryset = queryset.filter(article_subject_relevances__subject=subject).distinct()
    
    # Get relevant and not relevant articles via ArticleSubjectRelevance
    return queryset.select_related().prefetch_related(
        'article_subject_relevances'
    )


def build_dataset(queryset: QuerySet) -> pd.DataFrame:
    """
    Build a dataset from a queryset of articles, merging title and summary and 
    including relevance labels.
    
    Args:
        queryset (QuerySet): The queryset of Articles objects
    
    Returns:
        pd.DataFrame: DataFrame with columns:
            - article_id: The article's primary key
            - text: Combined title and summary
            - relevant: Boolean indicating relevance
            
    Note:
        Articles without relevance labels will be dropped.
    """
    data = []
    
    for article in queryset:
        # Get the relevance information for this article
        relevance_entries = article.article_subject_relevances.all()
        
        if not relevance_entries.exists():
            continue  # Skip articles without relevance information
            
        # Use the first relevance entry (there should be one per subject)
        relevance = relevance_entries.first()
        
        # Combine title and summary
        text = article.title
        if article.summary:
            text += " " + article.summary
            
        data.append({
            'article_id': article.article_id,
            'text': text,
            'relevant': int(relevance.is_relevant)  # Convert boolean to 0/1
        })
    
    # Create DataFrame
    df = pd.DataFrame(data)
    
    # Drop any rows with missing data
    df = df.dropna()
    
    return df


def train_val_test_split(
    df: pd.DataFrame, 
    stratify_col: str = 'relevant',
    train_size: float = 0.7,
    val_size: float = 0.15,
    test_size: float = 0.15,
    random_state: int = 69
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Split a DataFrame into training, validation, and testing sets with stratification.
    
    Args:
        df (pd.DataFrame): Input DataFrame to split
        stratify_col (str, optional): Column to use for stratification. Defaults to 'relevant'.
        train_size (float, optional): Proportion for training set. Defaults to 0.7.
        val_size (float, optional): Proportion for validation set. Defaults to 0.15.
        test_size (float, optional): Proportion for test set. Defaults to 0.15.
        random_state (int, optional): Random seed for reproducibility. Defaults to 69.
    
    Returns:
        Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]: Train, validation, and test DataFrames
        
    Raises:
        ValueError: If stratify column has only one unique value or 
                   if the sum of train_size, val_size, and test_size isn't 1.0
    """
    # Check that proportions sum to 1
    if abs(train_size + val_size + test_size - 1.0) > 1e-6:
        raise ValueError("train_size, val_size, and test_size must sum to 1.0")
    
    if len(df) == 0:
        raise ValueError("Dataset is empty")
    
    # Check if we have enough samples per class for stratified splitting
    # Handle missing values and non-numeric data in the stratify column
    stratify_col_data = df[stratify_col].dropna()
    
    # Ensure we have valid data to work with
    if len(stratify_col_data) == 0:
        raise ValueError(f"No valid data in stratify column '{stratify_col}'")
    
    try:
        # Get distribution of classes
        class_counts = stratify_col_data.value_counts()
        num_classes = len(class_counts)
        
        # Get minimum class count with careful error handling
        if num_classes == 0:
            raise ValueError(f"No valid classes found in column '{stratify_col}'")
            
        min_class_count = class_counts.min()
        
        # Log the actual class distribution for debugging
        class_distribution = ", ".join([f"{k}: {v}" for k, v in class_counts.items()])
        print(f"Class distribution in dataset: {class_distribution}")
        
    except Exception as e:
        # Fallback in case of any error processing class counts
        raise ValueError(f"Error analyzing class distribution: {str(e)}")
    
    # Check for extreme cases: not enough classes or extremely small class size
    if num_classes < 2:
        raise ValueError(f"Dataset has only one class: {class_counts.to_dict()}. " +
                         "Cannot perform classification with only one class.")
    
    # Special handling for datasets with very few examples per class
    if min_class_count < 2:
        raise ValueError(f"The least populated class in y has only {min_class_count} member, which is too few. " +
                         "Consider collecting more data for this class. Class distribution: {class_counts.to_dict()}")
    
    # We already ensure min_class_count >= 2 above, but this is extra insurance
    # If any class has fewer than 3 samples, we can't do proper stratified splitting
    # (we need at least 1 for train, 1 for val, 1 for test)
    if min_class_count < 3:
        print(f"WARNING: Class with only {min_class_count} examples detected. Using non-stratified splitting.")
        
        # Force non-stratified splitting for all small datasets
        rest_df, test_df = train_test_split(
            df, test_size=test_size, random_state=random_state, 
            shuffle=True, stratify=None  # Explicitly disable stratification
        )
        
        # Further split rest into train and validation
        val_size_adjusted = val_size / (1 - test_size)
        train_df, val_df = train_test_split(
            rest_df, test_size=val_size_adjusted, 
            random_state=random_state, shuffle=True, 
            stratify=None  # Explicitly disable stratification
        )
        
        # Verify the split worked
        print(f"Split complete - train: {len(train_df)}, val: {len(val_df)}, test: {len(test_df)}")
        
        return train_df, val_df, test_df
        
        # Handle class imbalance situations with non-stratified sampling
        print(f"Using non-stratified splitting due to limited examples per class")
        
        # Drop stratification explicitly
        rest_df, test_df = train_test_split(
            df, test_size=test_size, random_state=random_state, 
            shuffle=True, stratify=None  # Explicitly disable stratification
        )
        
        # Further split rest into train and validation
        val_size_adjusted = val_size / (1 - test_size)
        train_df, val_df = train_test_split(
            rest_df, test_size=val_size_adjusted, 
            random_state=random_state, shuffle=True, 
            stratify=None  # Explicitly disable stratification
        )
        
        # Double-check the generated splits for debugging
        for split_name, split_df in [("train", train_df), ("val", val_df), ("test", test_df)]:
            split_counts = split_df[stratify_col].value_counts()
            print(f"{split_name} split class distribution: {split_counts.to_dict()}")
        
        return train_df, val_df, test_df
    
    # Regular case: stratified split
    # First split: separate training set
    train_df, temp_df = train_test_split(
        df, 
        train_size=train_size,
        stratify=df[stratify_col],
        random_state=random_state
    )
    
    # Calculate the relative sizes for val and test sets
    relative_val_size = val_size / (val_size + test_size)
    
    # Second split: separate validation and test sets
    val_df, test_df = train_test_split(
        temp_df,
        train_size=relative_val_size,
        stratify=temp_df[stratify_col],
        random_state=random_state
    )
    
    return train_df, val_df, test_df
