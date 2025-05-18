"""
Unit tests for the ML package's factory function.

This module tests the get_trainer factory function to ensure it correctly returns
the appropriate trainer class based on the algorithm name.
"""
import unittest
from unittest.mock import patch

from gregory.ml import get_trainer
from gregory.ml.bert_wrapper import BertTrainer
from gregory.ml.lgbm_wrapper import LGBMTfidfTrainer
from gregory.ml.lstm_wrapper import LSTMTrainer


class TestMLFactory(unittest.TestCase):
    """Test cases for the ML factory function."""
    
    @patch('gregory.ml.bert_wrapper.TFAutoModel')
    @patch('gregory.ml.bert_wrapper.AutoTokenizer')
    def test_get_bert_trainer(self, mock_tokenizer, mock_model):
        """Test that get_trainer returns BertTrainer for 'pubmed_bert'."""
        trainer = get_trainer('pubmed_bert')
        self.assertIsInstance(trainer, BertTrainer)
        
        # Test with custom parameters
        custom_trainer = get_trainer('pubmed_bert', max_len=512, learning_rate=1e-5)
        self.assertIsInstance(custom_trainer, BertTrainer)
        self.assertEqual(custom_trainer.max_len, 512)
        self.assertEqual(custom_trainer.learning_rate, 1e-5)
    
    def test_get_lgbm_trainer(self):
        """Test that get_trainer returns LGBMTfidfTrainer for 'lgbm_tfidf'."""
        trainer = get_trainer('lgbm_tfidf')
        self.assertIsInstance(trainer, LGBMTfidfTrainer)
        
        # Test with custom parameters
        custom_trainer = get_trainer('lgbm_tfidf', random_state=42)
        self.assertIsInstance(custom_trainer, LGBMTfidfTrainer)
        self.assertEqual(custom_trainer.random_state, 42)
    
    @patch('gregory.ml.lstm_wrapper.TextVectorization')
    def test_get_lstm_trainer(self, mock_vectorization):
        """Test that get_trainer returns LSTMTrainer for 'lstm'."""
        trainer = get_trainer('lstm')
        self.assertIsInstance(trainer, LSTMTrainer)
        
        # Test with custom parameters
        custom_trainer = get_trainer('lstm', lstm_units=128, embedding_dim=256)
        self.assertIsInstance(custom_trainer, LSTMTrainer)
        self.assertEqual(custom_trainer.lstm_units, 128)
        self.assertEqual(custom_trainer.embedding_dim, 256)
    
    def test_invalid_algorithm(self):
        """Test that get_trainer raises ValueError for invalid algorithm."""
        with self.assertRaises(ValueError):
            get_trainer('invalid_algo')
        
        with self.assertRaises(ValueError):
            get_trainer('xgboost')  # Not a supported algorithm


if __name__ == '__main__':
    unittest.main()
