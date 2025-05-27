"""
Versioning utilities for model artifacts.

This module provides functions to create version-specific paths for model artifacts,
using a date-based naming scheme with auto-incrementing suffixes to avoid collisions.
"""
from datetime import datetime
from pathlib import Path
from typing import Union, Optional


def make_version_path(base_dir: Union[str, Path], team: str, subject: str, algo: str) -> Path:
    """
    Create a versioned path for saving model artifacts.
    
    The function creates a directory structure: base_dir/team/subject/algo/YYYYMMDD
    If the directory already exists, it adds a suffix _2, _3, etc.
    
    Args:
        base_dir (Union[str, Path]): The base directory for all model artifacts
        team (str): Team identifier/name
        subject (str): Subject identifier/name
        algo (str): Algorithm identifier/name (e.g., 'pubmed_bert', 'lgbm_tfidf', 'lstm')
        
    Returns:
        Path: The final path as a Path object with directories created
        
    Examples:
        >>> base_dir = Path('/models')
        >>> path = make_version_path(base_dir, 'team1', 'covid', 'pubmed_bert')
        >>> path
        PosixPath('/models/team1/covid/pubmed_bert/20250518')
        
        # If the directory already exists, it adds a suffix:
        >>> path = make_version_path(base_dir, 'team1', 'covid', 'pubmed_bert')
        >>> path
        PosixPath('/models/team1/covid/pubmed_bert/20250518_2')
    """
    # Ensure base_dir is a Path object
    base_dir = Path(base_dir)
    
    # Create the directory structure up to the algorithm level
    algo_dir = base_dir / team / subject / algo
    algo_dir.mkdir(parents=True, exist_ok=True)
    
    # Get today's date in YYYYMMDD format
    today = datetime.now().strftime("%Y%m%d")
    
    # Try the base version (without suffix)
    version_dir = algo_dir / today
    
    # If the directory exists, try adding suffixes
    suffix = 1
    while version_dir.exists():
        suffix += 1
        version_dir = algo_dir / f"{today}_{suffix}"
    
    # Create the version directory
    version_dir.mkdir(parents=False, exist_ok=False)
    
    return version_dir
