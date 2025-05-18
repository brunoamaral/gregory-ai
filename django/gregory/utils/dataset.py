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
    subject = Subject.objects.get(slug=subject_slug, team=team)
    
    # Base queryset filtering for team and subject
    queryset = Articles.objects.filter(teams=team, subjects=subject)
    
    # Add time window filter if specified
    if window_days is not None:
        cutoff_date = datetime.now() - timedelta(days=window_days)
        queryset = queryset.filter(discovery_date__gte=cutoff_date)
    
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
    
    # Check that we have enough data to stratify
    if df[stratify_col].nunique() <= 1:
        raise ValueError(f"Cannot stratify: '{stratify_col}' has only {df[stratify_col].nunique()} unique value(s)")
    
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
