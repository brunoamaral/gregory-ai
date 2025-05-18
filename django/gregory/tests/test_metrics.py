"""
Unit tests for the metrics module.

This module tests the evaluate_binary function against scikit-learn's implementations
to ensure that the metrics are calculated correctly.
"""
import numpy as np
import pytest
from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    roc_auc_score,
    average_precision_score
)

from gregory.utils.metrics import evaluate_binary


class TestEvaluateBinary:
    """Test cases for evaluate_binary function."""

    def test_perfect_prediction(self):
        """Test with perfect predictions."""
        y_true = np.array([0, 1, 0, 1, 1])
        y_prob = np.array([0.1, 0.9, 0.2, 0.8, 0.7])
        
        result = evaluate_binary(y_true, y_prob, threshold=0.5)
        
        assert result["accuracy"] == 1.0
        assert result["precision"] == 1.0
        assert result["recall"] == 1.0
        assert result["f1"] == 1.0
        assert result["roc_auc"] == 1.0
        assert result["pr_auc"] == 1.0
    
    def test_with_prefix(self):
        """Test with prefix applied to keys."""
        y_true = np.array([0, 1, 0, 1])
        y_prob = np.array([0.1, 0.9, 0.2, 0.8])
        
        result = evaluate_binary(y_true, y_prob, threshold=0.5, prefix="test_")
        
        assert "test_accuracy" in result
        assert "test_precision" in result
        assert "test_recall" in result
        assert "test_f1" in result
        assert "test_roc_auc" in result
        assert "test_pr_auc" in result
    
    def test_against_sklearn(self):
        """Test against sklearn's implementations with realistic data."""
        # Generate synthetic data with some errors
        np.random.seed(42)  # for reproducibility
        y_true = np.random.randint(0, 2, size=100)
        y_prob = np.random.beta(2, 5, size=100)  # Beta distribution for probabilities
        threshold = 0.3
        
        # Calculate using our function
        result = evaluate_binary(y_true, y_prob, threshold=threshold)
        
        # Calculate using sklearn directly
        y_pred = (y_prob >= threshold).astype(int)
        expected_accuracy = accuracy_score(y_true, y_pred)
        expected_precision = precision_score(y_true, y_pred, zero_division=0)
        expected_recall = recall_score(y_true, y_pred, zero_division=0)
        expected_f1 = f1_score(y_true, y_pred, zero_division=0)
        expected_roc_auc = roc_auc_score(y_true, y_prob)
        expected_pr_auc = average_precision_score(y_true, y_prob)
        
        # Assert that our results match sklearn's up to 1e-6 precision
        assert abs(result["accuracy"] - expected_accuracy) < 1e-6
        assert abs(result["precision"] - expected_precision) < 1e-6
        assert abs(result["recall"] - expected_recall) < 1e-6
        assert abs(result["f1"] - expected_f1) < 1e-6
        assert abs(result["roc_auc"] - expected_roc_auc) < 1e-6
        assert abs(result["pr_auc"] - expected_pr_auc) < 1e-6
    
    def test_different_thresholds(self):
        """Test with different threshold values."""
        y_true = np.array([0, 1, 0, 1, 1, 0])
        y_prob = np.array([0.3, 0.7, 0.4, 0.6, 0.8, 0.2])
        
        # With threshold 0.5
        result_t5 = evaluate_binary(y_true, y_prob, threshold=0.5)
        
        # With threshold 0.7
        result_t7 = evaluate_binary(y_true, y_prob, threshold=0.7)
        
        # Verify that different thresholds produce different results
        assert result_t5 != result_t7
        
        # Manually check predictions at threshold 0.7
        y_pred_t7 = (y_prob >= 0.7).astype(int)
        expected_accuracy_t7 = accuracy_score(y_true, y_pred_t7)
        
        assert abs(result_t7["accuracy"] - expected_accuracy_t7) < 1e-6
    
    def test_empty_arrays(self):
        """Test with empty arrays (should raise ValueError)."""
        with pytest.raises(ValueError):
            evaluate_binary(np.array([]), np.array([]))
    
    def test_incompatible_shapes(self):
        """Test with incompatible shapes (should raise ValueError)."""
        with pytest.raises(ValueError, match="must have the same shape"):
            evaluate_binary(np.array([0, 1]), np.array([0.1, 0.2, 0.3]))
    
    def test_non_binary_labels(self):
        """Test with non-binary labels (should raise ValueError)."""
        with pytest.raises(ValueError, match="must contain only binary labels"):
            evaluate_binary(np.array([0, 1, 2]), np.array([0.1, 0.2, 0.3]))
    
    def test_invalid_probabilities(self):
        """Test with invalid probabilities (should raise ValueError)."""
        with pytest.raises(ValueError, match="must contain probabilities"):
            evaluate_binary(np.array([0, 1]), np.array([0.1, 1.2]))
    
    def test_edge_case_all_same_class(self):
        """Test with all samples belonging to the same class."""
        # All negative samples
        y_true_neg = np.zeros(10)
        y_prob_neg = np.array([0.1, 0.2, 0.3, 0.4, 0.1, 0.2, 0.3, 0.4, 0.1, 0.2])
        
        result_neg = evaluate_binary(y_true_neg, y_prob_neg)
        
        # For all negative samples, precision, recall, and f1 should be 0 (with zero_division=0)
        assert result_neg["precision"] == 0
        assert result_neg["recall"] == 0
        assert result_neg["f1"] == 0
        
        # All positive samples
        y_true_pos = np.ones(10)
        y_prob_pos = np.array([0.6, 0.7, 0.8, 0.9, 0.6, 0.7, 0.8, 0.9, 0.6, 0.7])
        
        result_pos = evaluate_binary(y_true_pos, y_prob_pos)
        
        # For all positive samples with high probabilities, precision, recall should be 1
        assert result_pos["precision"] == 1
        assert result_pos["recall"] == 1
