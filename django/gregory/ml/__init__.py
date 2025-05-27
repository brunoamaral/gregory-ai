"""
Machine Learning package for Gregory AI.

This package contains machine learning algorithms and utilities for the Gregory AI project,
including wrappers for different model architectures, training pipelines, and evaluation tools.
It also provides utilities for pseudo-labeling and semi-supervised learning.
"""
# Export public API
from gregory.ml.trainer import get_trainer
from gregory.ml.pseudo import (
    generate_pseudo_labels,
    save_pseudo_csv,
    get_pseudo_label_stats,
    load_and_filter_pseudo_labels
)
