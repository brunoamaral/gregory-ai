"""
Machine Learning package for Gregory AI.

This package contains machine learning algorithms and utilities for the Gregory AI project,
including wrappers for different model architectures, training pipelines, and evaluation tools.
It also provides utilities for pseudo-labeling and semi-supervised learning.
"""
from typing import Union, Dict, Any, Type, List

from gregory.ml.bert_wrapper import BertTrainer
from gregory.ml.lgbm_wrapper import LGBMTfidfTrainer
from gregory.ml.lstm_wrapper import LSTMTrainer
from gregory.ml.pseudo import (
    generate_pseudo_labels,
    save_pseudo_csv,
    get_pseudo_label_stats,
    load_and_filter_pseudo_labels
)


def get_trainer(algo: str, **kwargs: Any) -> Union[BertTrainer, LGBMTfidfTrainer, LSTMTrainer]:
    """
    Factory function to get the appropriate model trainer based on algorithm name.
    
    Args:
        algo (str): Algorithm identifier. Must be one of:
            - 'pubmed_bert': BERT-based model for biomedical text
            - 'lgbm_tfidf': LightGBM with TF-IDF features
            - 'lstm': LSTM neural network
        **kwargs (Any): Additional parameters to pass to the trainer constructor
    
    Returns:
        Union[BertTrainer, LGBMTfidfTrainer, LSTMTrainer]: An instance of the appropriate trainer
        
    Raises:
        ValueError: If the algorithm is not one of the supported types
    
    Examples:
        >>> trainer = get_trainer('pubmed_bert', max_len=512)
        >>> trainer = get_trainer('lgbm_tfidf', random_state=42)
        >>> trainer = get_trainer('lstm', lstm_units=128)
    """
    algo_map: Dict[str, Type] = {
        'pubmed_bert': BertTrainer,
        'lgbm_tfidf': LGBMTfidfTrainer,
        'lstm': LSTMTrainer,
    }
    
    if algo not in algo_map:
        valid_algos = ", ".join(f"'{k}'" for k in algo_map.keys())
        raise ValueError(f"Unknown algorithm '{algo}'. Must be one of: {valid_algos}")
    
    return algo_map[algo](**kwargs)
