"""
Unit tests for the BertTrainer class.

This module contains tests for the BertTrainer class functionality, including
model creation, text encoding, training, evaluation, and saving/loading.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import tensorflow as tf

try:
    # Try to use tf-keras for backward compatibility with transformers
    from tf_keras.layers import Layer
except ImportError:
    # Fall back to TensorFlow's keras if tf-keras is not available
    from tensorflow.keras.layers import Layer

from gregory.ml.bert_wrapper import BertTrainer


class TestBertTrainer(unittest.TestCase):
    """Test cases for the BertTrainer class."""
    
    def setUp(self):
        """Set up test fixtures."""
        # Mock the BERT model and tokenizer to avoid loading large models during testing
        self.mock_bert_patcher = patch('gregory.ml.bert_wrapper.TFAutoModel')
        self.mock_tokenizer_patcher = patch('gregory.ml.bert_wrapper.AutoTokenizer')
        
        self.mock_bert = self.mock_bert_patcher.start()
        self.mock_tokenizer = self.mock_tokenizer_patcher.start()
        
        # Set up mock returns
        self.mock_bert.from_pretrained.return_value = MagicMock()
        self.mock_tokenizer.from_pretrained.return_value = MagicMock()
        
        # Configure the tokenizer mock to return sensible values
        self.mock_tokenizer_instance = self.mock_tokenizer.from_pretrained.return_value
        self.mock_tokenizer_instance.encode_plus.return_value = {
            'input_ids': tf.ones((1, 10), dtype=tf.int32),
            'attention_mask': tf.ones((1, 10), dtype=tf.int32)
        }
        
        # Configure the BERT mock to return sensible values
        self.mock_bert_instance = self.mock_bert.from_pretrained.return_value
        
        # Create a mock for the BERT model call
        def mock_bert_call(inputs, attention_mask=None, **kwargs):
            batch_size = 2  # Just use a fixed batch size for testing
            # Return a tuple with sequence output and pooled output
            sequence_output = tf.constant(np.ones((batch_size, 10, 768)), dtype=tf.float32)
            pooled_output = tf.constant(np.ones((batch_size, 768)), dtype=tf.float32)
            return [sequence_output, pooled_output]
        
        # Set the mock call method
        self.mock_bert_instance.side_effect = mock_bert_call
        
        # Also patch these layers to avoid TensorFlow operations
        self.dense_patcher = patch('gregory.ml.bert_wrapper.Dense')
        self.dropout_patcher = patch('gregory.ml.bert_wrapper.Dropout')
        self.model_patcher = patch('gregory.ml.bert_wrapper.Model')
        
        # Start the patchers
        self.mock_dense = self.dense_patcher.start()
        self.mock_dropout = self.dropout_patcher.start()
        self.mock_model = self.model_patcher.start()
        
        # Configure the mocks to return appropriate values
        self.mock_dense_instance = MagicMock()
        self.mock_dropout_instance = MagicMock()
        self.mock_model_instance = MagicMock()
        
        # Set up return values for the Dense and Dropout layer mocks
        self.mock_dense.return_value = self.mock_dense_instance
        self.mock_dropout.return_value = self.mock_dropout_instance
        self.mock_model.return_value = self.mock_model_instance
        
        # Make the Dense layer callable and return a tensor
        self.mock_dense_instance.side_effect = lambda x: tf.constant(np.random.rand(2, 2), dtype=tf.float32)
        
        # Make the Dropout layer callable and return a tensor
        self.mock_dropout_instance.side_effect = lambda x: tf.constant(np.random.rand(2, 2), dtype=tf.float32)
        
        # Setup the model instance with inputs and outputs attributes
        self.mock_model_instance.inputs = [MagicMock(), MagicMock()]
        self.mock_model_instance.outputs = [tf.constant(np.random.rand(2, 2), dtype=tf.float32)]
        
        # Create a BertTrainer instance with small dimensions for testing
        self.trainer = BertTrainer(
            max_len=10,
            bert_model_name='test-model',
            learning_rate=1e-4,
            dense_units=16,
            freeze_weights=True
        )
    
    def tearDown(self):
        """Clean up after tests."""
        self.mock_bert_patcher.stop()
        self.mock_tokenizer_patcher.stop()
        self.dense_patcher.stop()
        self.dropout_patcher.stop()
        self.model_patcher.stop()
    
    def test_init(self):
        """Test initialization of BertTrainer."""
        self.assertEqual(self.trainer.max_len, 10)
        self.assertEqual(self.trainer.bert_model_name, 'test-model')
        self.assertEqual(self.trainer.learning_rate, 1e-4)
        self.assertEqual(self.trainer.dense_units, 16)
        self.assertTrue(self.trainer.freeze_weights)
        self.assertIsNotNone(self.trainer.model)
        self.assertIsNone(self.trainer.history)
        self.assertIsNone(self.trainer.training_time)
        self.assertIsNone(self.trainer.epochs_trained)
    
    def test_create_model(self):
        """Test model creation."""
        model = self.trainer._create_model()
        # Since we're mocking the Model class, we can't use assertIsInstance directly
        # Instead, we'll verify it's the mock we expect
        self.assertEqual(model, self.mock_model_instance)
        self.assertEqual(len(model.inputs), 2)  # input_ids and attention_masks
        self.assertEqual(model.outputs[0].shape[-1], 2)  # Binary classification
    
    def test_encode_texts(self):
        """Test text encoding."""
        texts = ["This is a test", "Another test"]
        input_ids, attention_masks = self.trainer.encode_texts(texts)
        
        self.assertIsInstance(input_ids, tf.Tensor)
        self.assertIsInstance(attention_masks, tf.Tensor)
        self.assertEqual(input_ids.shape[0], 2)  # Batch size of 2
        self.assertEqual(attention_masks.shape[0], 2)  # Batch size of 2
    
    @patch('gregory.ml.bert_wrapper.time')
    def test_train(self, mock_time):
        """Test model training."""
        # Setup time mock
        mock_time.time.side_effect = [0, 10]  # 10 seconds of training time
        
        # Mock fit method to return a history object
        with patch.object(self.trainer.model, 'fit') as mock_fit:
            mock_history = MagicMock()
            mock_history.history = {
                'loss': [0.5, 0.4, 0.3],
                'val_loss': [0.6, 0.5, 0.4],
                'accuracy': [0.7, 0.8, 0.9],
                'val_accuracy': [0.6, 0.7, 0.8]
            }
            mock_fit.return_value = mock_history
            
            # Call train method
            history = self.trainer.train(
                train_texts=["Text 1", "Text 2"],
                train_labels=[0, 1],
                val_texts=["Val 1", "Val 2"],
                val_labels=[1, 0],
                epochs=3,
                batch_size=2
            )
            
            # Verify results
            self.assertEqual(self.trainer.training_time, 10)
            self.assertEqual(self.trainer.epochs_trained, 3)
            self.assertEqual(history, mock_history)
    
    def test_evaluate(self):
        """Test model evaluation."""
        # Mock predict method
        with patch.object(self.trainer.model, 'predict') as mock_predict, \
             patch.object(self.trainer.model, 'evaluate') as mock_evaluate:
            
            # Configure mocks
            mock_predict.return_value = np.array([[0.2, 0.8], [0.9, 0.1]])
            mock_evaluate.return_value = {'accuracy': 0.5, 'loss': 0.3}
            
            # Call evaluate method
            metrics = self.trainer.evaluate(
                test_texts=["Test 1", "Test 2"],
                test_labels=[1, 0],
                threshold=0.5
            )
            
            # Verify results
            self.assertIn('accuracy', metrics)
            self.assertIn('precision', metrics)
            self.assertIn('recall', metrics)
            self.assertIn('f1', metrics)
            self.assertIn('roc_auc', metrics)
    
    def test_predict(self):
        """Test model prediction."""
        # Mock predict method
        with patch.object(self.trainer.model, 'predict') as mock_predict:
            # Configure mock
            mock_predict.return_value = np.array([[0.2, 0.8], [0.9, 0.1]])
            
            # Call predict method
            predictions, probabilities = self.trainer.predict(
                texts=["Text 1", "Text 2"],
                threshold=0.5
            )
            
            # Verify results
            self.assertEqual(predictions, [1, 0])
            self.assertEqual(probabilities, [0.8, 0.1])
    
    def test_save(self):
        """Test model saving."""
        # Create a temporary directory for saving
        with tempfile.TemporaryDirectory() as temp_dir:
            # Mock save_weights method
            with patch.object(self.trainer.model, 'save_weights') as mock_save:
                # Set history attribute
                self.trainer.history = MagicMock()
                self.trainer.history.history = {
                    'loss': [0.5, 0.4],
                    'val_loss': [0.6, 0.5],
                    'accuracy': [0.7, 0.8],
                    'val_accuracy': [0.6, 0.7]
                }
                self.trainer.training_time = 10
                self.trainer.epochs_trained = 2
                
                # Call save method
                save_info = self.trainer.save(temp_dir)
                
                # Verify results
                self.assertTrue(mock_save.called)
                self.assertIn('weights_path', save_info)
                self.assertIn('metrics_info', save_info)
                
                # Check metrics file was created
                metrics_path = Path(temp_dir) / "metrics.json"
                self.assertTrue(metrics_path.exists())
    
    def test_load_weights(self):
        """Test loading model weights."""
        # Mock load_weights method
        with patch.object(self.trainer.model, 'load_weights') as mock_load:
            # Call load_weights method
            self.trainer.load_weights("fake_path.h5")
            
            # Verify results
            mock_load.assert_called_once_with("fake_path.h5")
    
    def test_perform_pseudo_labeling(self):
        """Test pseudo-labeling functionality."""
        # Mock methods
        with patch.object(self.trainer, '_create_model') as mock_create, \
             patch.object(self.trainer, 'encode_texts') as mock_encode, \
             patch.object(self.trainer.model, 'fit') as mock_fit, \
             patch.object(self.trainer.model, 'predict') as mock_predict:
            
            # Configure mocks
            mock_create.return_value = self.trainer.model
            mock_encode.return_value = (
                tf.ones((2, 10), dtype=tf.int32),
                tf.ones((2, 10), dtype=tf.int32)
            )
            mock_fit.return_value = MagicMock()
            
            # First call: confident predictions
            # Second call: no confident predictions (to test early stopping)
            mock_predict.side_effect = [
                np.array([[0.05, 0.95], [0.1, 0.9]]),  # Iteration 1: both confident
                np.array([[0.5, 0.5], [0.6, 0.4]])     # Iteration 2: none confident
            ]
            
            # Call perform_pseudo_labeling
            labeled_texts, labeled_labels = self.trainer.perform_pseudo_labeling(
                labeled_texts=["Labeled 1", "Labeled 2"],
                labeled_labels=[0, 1],
                unlabeled_texts=["Unlabeled 1", "Unlabeled 2"],
                val_texts=["Val 1", "Val 2"],
                val_labels=[0, 1],
                confidence_threshold=0.9,
                max_iterations=3,
                batch_size=2,
                epochs_per_iter=1
            )
            
            # Verify results
            self.assertEqual(len(labeled_texts), 4)  # Original 2 + 2 pseudo-labeled
            self.assertEqual(len(labeled_labels), 4)


if __name__ == '__main__':
    unittest.main()
