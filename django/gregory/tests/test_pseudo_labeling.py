"""
Tests for pseudo_labeling utility functions.
"""

import unittest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock, patch
from django.test import TestCase

from gregory.utils.pseudo_labeling import (
    apply_pseudo_labeling,
    cap_pseudo_labels,
    get_pseudo_labeled_data
)


class MockModel:
    """Mock model class for testing pseudo-labeling."""
    
    def __init__(self, probabilities):
        self.probabilities = probabilities
    
    def predict_proba(self, X):
        return self.probabilities
    
    def predict(self, X):
        return np.argmax(self.probabilities, axis=1)


class PseudoLabelingTests(TestCase):
    """Test cases for pseudo-labeling utility functions."""
    
    def setUp(self):
        """Set up test data."""
        # Create unlabeled data
        self.unlabeled_df = pd.DataFrame({
            'article_id': [1, 2, 3, 4, 5, 6],
            'cleaned_text': [
                'sample text 1', 'sample text 2', 'sample text 3',
                'sample text 4', 'sample text 5', 'sample text 6'
            ]
        })
        # Note: pseudo-labeling is now enabled by default in the command
        
        # Create labeled data with equal class distribution
        self.labeled_df = pd.DataFrame({
            'article_id': [7, 8, 9, 10],
            'cleaned_text': [
                'labeled text 1', 'labeled text 2',
                'labeled text 3', 'labeled text 4'
            ],
            'is_relevant': [1, 1, 0, 0]
        })
        
        # Create mock probabilities
        # Each row: [probability for class 0, probability for class 1]
        self.mock_probabilities = np.array([
            [0.05, 0.95],  # High confidence for class 1
            [0.92, 0.08],  # High confidence for class 0
            [0.40, 0.60],  # Low confidence for class 1
            [0.55, 0.45],  # Low confidence for class 0
            [0.02, 0.98],  # High confidence for class 1
            [0.96, 0.04],  # High confidence for class 0
        ])
        
        # Mock vectorizer that returns a dummy matrix
        self.mock_vectorizer = MagicMock()
        self.mock_vectorizer.transform = MagicMock(return_value=np.array([[0, 0, 0]] * 6))
        
        # Create mock model using the probabilities
        self.mock_model = MockModel(self.mock_probabilities)
    
    def test_apply_pseudo_labeling(self):
        """Test applying pseudo-labeling with different thresholds."""
        # With high threshold (0.9)
        high_conf_df = apply_pseudo_labeling(
            self.unlabeled_df, 
            self.mock_model, 
            self.mock_vectorizer,
            self.labeled_df,
            confidence_threshold=0.9
        )
        
        # Should select 4 samples: index 0, 1, 4, 5
        self.assertEqual(len(high_conf_df), 4)
        self.assertListEqual(list(high_conf_df['is_relevant']), [1, 0, 1, 0])
        
        # With lower threshold (0.8)
        medium_conf_df = apply_pseudo_labeling(
            self.unlabeled_df, 
            self.mock_model, 
            self.mock_vectorizer,
            self.labeled_df,
            confidence_threshold=0.8
        )
        
        # Should still select the same 4 samples
        self.assertEqual(len(medium_conf_df), 4)
        
        # With very low threshold (0.5)
        low_conf_df = apply_pseudo_labeling(
            self.unlabeled_df, 
            self.mock_model, 
            self.mock_vectorizer,
            self.labeled_df,
            confidence_threshold=0.5
        )
        
        # Should select all 6 samples
        self.assertEqual(len(low_conf_df), 6)
    
    def test_cap_pseudo_labels(self):
        """Test capping pseudo-labeled examples per class."""
        # Create a DataFrame with pseudo-labeled data
        pseudo_df = pd.DataFrame({
            'article_id': [1, 2, 3, 4, 5, 6],
            'cleaned_text': [
                'sample text 1', 'sample text 2', 'sample text 3',
                'sample text 4', 'sample text 5', 'sample text 6'
            ],
            'is_relevant': [1, 0, 1, 0, 1, 0],  # 3 of each class
            'confidence': [0.95, 0.92, 0.85, 0.75, 0.98, 0.96]  # Confidence scores
        })
        
        # Cap based on the labeled_df which has 2 samples per class
        capped_df = cap_pseudo_labels(pseudo_df, self.labeled_df)
        
        # Should have 4 samples total (2 from each class)
        self.assertEqual(len(capped_df), 4)
        
        # Should select the highest confidence samples
        # Class 1: confidence 0.98, 0.95
        # Class 0: confidence 0.96, 0.92
        relevant_samples = capped_df[capped_df['is_relevant'] == 1]
        irrelevant_samples = capped_df[capped_df['is_relevant'] == 0]
        
        self.assertEqual(len(relevant_samples), 2)
        self.assertEqual(len(irrelevant_samples), 2)
        
        # Check that the highest confidence samples were selected
        self.assertIn(0.98, relevant_samples['confidence'].values)
        self.assertIn(0.95, relevant_samples['confidence'].values)
        self.assertIn(0.96, irrelevant_samples['confidence'].values)
        self.assertIn(0.92, irrelevant_samples['confidence'].values)
    
    def test_get_pseudo_labeled_data(self):
        """Test the full pseudo-labeling workflow."""
        pseudo_df = get_pseudo_labeled_data(
            self.unlabeled_df,
            self.mock_model,
            self.mock_vectorizer,
            self.labeled_df,
            confidence_threshold=0.9
        )
        
        # Should have 4 samples total (2 from each class)
        self.assertEqual(len(pseudo_df), 4)
        
        # Class distribution should match labeled_df
        self.assertEqual(sum(pseudo_df['is_relevant'] == 1), 2)
        self.assertEqual(sum(pseudo_df['is_relevant'] == 0), 2)
        
        # Confidence column should have been dropped
        self.assertNotIn('confidence', pseudo_df.columns)
    
    def test_empty_input_handling(self):
        """Test handling of empty input DataFrames."""
        # Empty unlabeled_df
        empty_df = pd.DataFrame()
        result = get_pseudo_labeled_data(
            empty_df,
            self.mock_model,
            self.mock_vectorizer,
            self.labeled_df
        )
        self.assertTrue(result.empty)
        
        # Model without predict_proba
        class SimpleMockModel:
            def predict(self, X):
                return np.array([0, 1, 0])
        
        simple_model = SimpleMockModel()
        result = get_pseudo_labeled_data(
            self.unlabeled_df,
            simple_model,
            self.mock_vectorizer,
            self.labeled_df
        )
        self.assertTrue(result.empty)


if __name__ == '__main__':
    unittest.main()
