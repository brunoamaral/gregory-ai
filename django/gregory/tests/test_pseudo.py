"""
Unit tests for the pseudo-labeling module.

This module tests the pseudo-labeling functionality, with a focus on:
1. CSV file saving logic with timestamp-based naming
2. Self-training loop functionality (using mocks)
3. Stats calculation and filtering functionality
"""
import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest
import tensorflow as tf

from gregory.ml.pseudo import (
    generate_pseudo_labels,
    save_pseudo_csv,
    get_pseudo_label_stats,
    load_and_filter_pseudo_labels
)


class TestSavePseudoCsv:
    """Tests for the save_pseudo_csv function."""
    
    def test_csv_path_creation(self):
        """Test that CSV file path is correctly created with timestamp."""
        # Create a temporary directory for testing
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create a small test DataFrame
            df = pd.DataFrame({
                'article_id': [1, 2, 3],
                'text': ['text1', 'text2', 'text3'],
                'relevant': [1, 0, 1],
                'pseudo_labelled': [False, False, False]
            })
            
            # Mock datetime to return a fixed timestamp for testing
            fixed_timestamp = "20250518_123456"
            with patch('gregory.ml.pseudo.datetime') as mock_datetime:
                mock_datetime.now.return_value = datetime.strptime(fixed_timestamp, "%Y%m%d_%H%M%S")
                mock_datetime.strftime = datetime.strftime
                
                # Save the CSV
                file_path = save_pseudo_csv(df, Path(tmp_dir), verbose=False)
                
                # Check that the file was created with the correct name
                expected_path = Path(tmp_dir) / f"{fixed_timestamp}.csv"
                assert file_path == expected_path
                assert file_path.exists()
                
                # Verify the content was saved correctly
                saved_df = pd.read_csv(file_path)
                assert len(saved_df) == len(df)
                assert list(saved_df.columns) == list(df.columns)
                
                # Test with prefix
                file_path_with_prefix = save_pseudo_csv(
                    df, Path(tmp_dir), prefix='test', verbose=False
                )
                expected_path_with_prefix = Path(tmp_dir) / f"test_{fixed_timestamp}.csv"
                assert file_path_with_prefix == expected_path_with_prefix
                assert file_path_with_prefix.exists()
    
    def test_csv_name_collision(self):
        """Test that suffix is added to avoid name collisions."""
        # Create a temporary directory for testing
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create a small test DataFrame
            df = pd.DataFrame({
                'article_id': [1, 2, 3],
                'text': ['text1', 'text2', 'text3'],
                'relevant': [1, 0, 1],
                'pseudo_labelled': [False, False, False]
            })
            
            # Mock datetime to return a fixed timestamp for testing
            fixed_timestamp = "20250518_123456"
            with patch('gregory.ml.pseudo.datetime') as mock_datetime:
                mock_datetime.now.return_value = datetime.strptime(fixed_timestamp, "%Y%m%d_%H%M%S")
                mock_datetime.strftime = datetime.strftime
                
                # Create a file with the timestamp name first to simulate a collision
                expected_path = Path(tmp_dir) / f"{fixed_timestamp}.csv"
                with open(expected_path, 'w') as f:
                    f.write("dummy,file\n1,2\n")
                
                # Save the first CSV (should get suffix _2)
                file_path_1 = save_pseudo_csv(df, Path(tmp_dir), verbose=False)
                expected_path_1 = Path(tmp_dir) / f"{fixed_timestamp}_2.csv"
                assert file_path_1 == expected_path_1
                assert file_path_1.exists()
                
                # Save a second CSV (should get suffix _3)
                file_path_2 = save_pseudo_csv(df, Path(tmp_dir), verbose=False)
                expected_path_2 = Path(tmp_dir) / f"{fixed_timestamp}_3.csv"
                assert file_path_2 == expected_path_2
                assert file_path_2.exists()
                
    def test_csv_without_timestamp(self):
        """Test saving without a timestamp."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create a small test DataFrame
            df = pd.DataFrame({
                'article_id': [1, 2, 3],
                'text': ['text1', 'text2', 'text3'],
                'relevant': [1, 0, 1],
                'pseudo_labelled': [False, False, False]
            })
            
            # Save without timestamp
            file_path = save_pseudo_csv(
                df, Path(tmp_dir), prefix='model1', include_timestamp=False, verbose=False
            )
            expected_path = Path(tmp_dir) / "model1.csv"
            assert file_path == expected_path
            
            # Without prefix or timestamp should use default name
            file_path2 = save_pseudo_csv(
                df, Path(tmp_dir), include_timestamp=False, verbose=False
            )
            expected_path2 = Path(tmp_dir) / "pseudo_labels.csv"
            assert file_path2 == expected_path2


class TestGeneratePseudoLabels:
    """Tests for the generate_pseudo_labels function."""
    
    @patch('gregory.ml.pseudo.get_trainer')
    def test_pseudo_labeling_loop(self, mock_get_trainer):
        """Test the pseudo-labeling loop logic using mocks."""
        # Set up mock
        mock_trainer_instance = MagicMock()
        mock_get_trainer.return_value = mock_trainer_instance
        
        # Configure predict method to return different confidence scores in each call
        # First call: high confidence for 3 examples
        # Second call: high confidence for 2 examples
        # Third call: no high confidence examples
        mock_trainer_instance.predict.side_effect = [
            ([1, 0, 1], [0.95, 0.1, 0.92]),  # First iteration: 2 confident examples
            ([1, 0], [0.91, 0.89]),          # Second iteration: 1 confident example
            ([0], [0.7]),                     # Third iteration: no confident examples
        ]
        
        # Create test data
        train_df = pd.DataFrame({
            'article_id': [1, 2],
            'text': ['labeled text 1', 'labeled text 2'],
            'relevant': [1, 0]
        })
        
        val_df = pd.DataFrame({
            'article_id': [3, 4],
            'text': ['val text 1', 'val text 2'],
            'relevant': [1, 0]
        })
        
        unlabelled_df = pd.DataFrame({
            'article_id': [5, 6, 7, 8, 9, 10],
            'text': [
                'unlabeled text 1',
                'unlabeled text 2',
                'unlabeled text 3',
                'unlabeled text 4',
                'unlabeled text 5',
                'unlabeled text 6',
            ]
        })
        
        # Call the function
        result_df = generate_pseudo_labels(
            train_df=train_df,
            val_df=val_df,
            unlabelled_df=unlabelled_df,
            confidence=0.9,
            max_iter=5,
            algorithm='pubmed_bert',
            verbose=False
        )
        
        # Assertions
        assert len(result_df) == 5  # Original 2 + 3 pseudo-labeled
        assert mock_trainer_instance.train.call_count == 3  # Should run 3 iterations
        
        # Check that the factory was called with the right algorithm
        mock_get_trainer.assert_called_with('pubmed_bert', **{
            'max_len': 400,
            'learning_rate': 2e-5,
            'dense_units': 48,
            'freeze_weights': True
        })
        
        # Check the pseudo-labeled data
        pseudo_rows = result_df[result_df['pseudo_labelled']]
        assert len(pseudo_rows) == 3
        assert set(pseudo_rows['pseudo_iteration']) == {1, 1, 2}  # 2 from first iter, 1 from second
        
        # Check that all pseudo-labeled rows have confidence values
        assert all(not pd.isna(row['pseudo_confidence']) for _, row in pseudo_rows.iterrows())
        
        # Test with a different algorithm
        mock_get_trainer.reset_mock()
        generate_pseudo_labels(
            train_df=train_df,
            val_df=val_df,
            unlabelled_df=pd.DataFrame({'text': ['test']}),
            algorithm='lgbm_tfidf',
            max_iter=1,
            verbose=False
        )
        # Check that factory was called with correct algorithm
        mock_get_trainer.assert_called_with('lgbm_tfidf', **{'random_state': 42})


class TestPseudoLabelStats:
    """Tests for the get_pseudo_label_stats function."""
    
    def test_stats_calculation(self):
        """Test that statistics are correctly calculated."""
        # Create a test DataFrame with both original and pseudo-labeled data
        df = pd.DataFrame({
            'text': ['text1', 'text2', 'text3', 'text4', 'text5'],
            'relevant': [1, 0, 1, 0, 1],
            'pseudo_labelled': [False, False, True, True, True],
            'confidence': [None, None, 0.92, 0.95, 0.91],
            'pseudo_iteration': [None, None, 1, 1, 2]
        })
        
        # Get stats
        stats = get_pseudo_label_stats(df)
        
        # Check the stats
        assert stats['total_examples'] == 5
        assert stats['original_examples'] == 2
        assert stats['pseudo_examples'] == 3
        assert stats['pseudo_examples_per_class'] == {0: 1, 1: 2}
        assert stats['pseudo_examples_per_iteration'] == {1: 2, 2: 1}
        assert stats['average_confidence'] == pytest.approx(0.9266, abs=1e-4)
        assert stats['min_confidence'] == pytest.approx(0.91, abs=1e-4)
        assert stats['max_confidence'] == pytest.approx(0.95, abs=1e-4)
        assert stats['class_distribution'] == {0: 2, 1: 3}


class TestLoadAndFilterPseudoLabels:
    """Tests for the load_and_filter_pseudo_labels function."""
    
    def test_load_and_filter(self):
        """Test loading and filtering pseudo-labeled data."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create a test DataFrame
            df = pd.DataFrame({
                'text': ['text1', 'text2', 'text3', 'text4', 'text5', 'text6'],
                'relevant': [1, 0, 1, 0, 1, 0],
                'pseudo_labelled': [False, False, True, True, True, True],
                'confidence': [None, None, 0.92, 0.95, 0.85, 0.82],
                'pseudo_iteration': [None, None, 1, 1, 2, 3]
            })
            
            # Save the DataFrame
            file_path = save_pseudo_csv(df, Path(tmp_dir), verbose=False)
            
            # Test loading with confidence filter
            filtered_high_conf = load_and_filter_pseudo_labels(file_path, min_confidence=0.9)
            assert len(filtered_high_conf) == 4  # 2 original + 2 high confidence
            assert sum(filtered_high_conf['pseudo_labelled']) == 2
            
            # Test loading with iteration filter
            filtered_early_iter = load_and_filter_pseudo_labels(file_path, max_iteration=1)
            assert len(filtered_early_iter) == 4  # 2 original + 2 from iteration 1
            assert sum(filtered_early_iter['pseudo_labelled']) == 2
            
            # Test loading with both filters
            filtered_both = load_and_filter_pseudo_labels(
                file_path, min_confidence=0.9, max_iteration=1
            )
            assert len(filtered_both) == 4  # 2 original + 2 high conf from iter 1
            assert sum(filtered_both['pseudo_labelled']) == 2
            
            # Test excluding original data
            only_pseudo = load_and_filter_pseudo_labels(file_path, include_original=False)
            assert len(only_pseudo) == 4  # Only pseudo-labeled examples
            assert all(only_pseudo['pseudo_labelled'])
    
    def test_error_handling(self):
        """Test error handling when loading invalid data."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create a DataFrame without pseudo-labeling info
            invalid_df = pd.DataFrame({
                'text': ['text1', 'text2'],
                'relevant': [1, 0]
            })
            
            # Save the invalid DataFrame
            invalid_path = Path(tmp_dir) / "invalid.csv"
            invalid_df.to_csv(invalid_path, index=False)
            
            # Test that loading raises an error
            with pytest.raises(ValueError, match="does not contain pseudo-labeling information"):
                load_and_filter_pseudo_labels(invalid_path)
