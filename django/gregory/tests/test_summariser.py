"""
Tests for the summariser module.
"""
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'admin.settings')
django.setup()

from unittest import mock
from django.test import TestCase
import torch
from gregory.utils.summariser import summarise, summarise_bulk


class MockedBartModel:
    """Mock BART model for testing summarization without loading the real model."""
    
    def __init__(self):
        pass
    
    def to(self, device):
        """Mock the to() method to move model to a device."""
        return self
    
    def generate(self, input_ids, **kwargs):
        """Mock the generate method to return a fixed output."""
        # Create a simple output tensor - just create IDs that will be decoded to "This is a summary."
        # The actual values don't matter as we'll mock the decode method too
        batch_size = input_ids.shape[0]
        return torch.tensor([[101, 102, 103, 104]] * batch_size)


class MockedBartTokenizer:
    """Mock BART tokenizer for testing summarization without loading the real model."""
    
    def __init__(self):
        pass
    
    def __call__(self, text, **kwargs):
        """Mock tokenization method."""
        if isinstance(text, str):
            # Single text case
            dummy_ids = torch.tensor([[1, 2, 3]])
            return {"input_ids": dummy_ids}
        else:
            # Batch case
            batch_size = len(text)
            dummy_ids = torch.tensor([[1, 2, 3]] * batch_size)
            return {"input_ids": dummy_ids}
    
    def decode(self, ids, **kwargs):
        """Mock decode method to return a fixed string."""
        # Return a simple summary
        return "This is a summary."


class SummariserTestCase(TestCase):
    """Test case for the summariser module."""
    
    def setUp(self):
        """Set up the test case with mocked BART model and tokenizer."""
        self.patcher = mock.patch('gregory.utils.summariser._initialize_model')
        self.mock_initialize = self.patcher.start()
        
        # Create mock objects
        self.mocked_model = MockedBartModel()
        self.mocked_tokenizer = MockedBartTokenizer()
        self.device = torch.device("cpu")
        
        # Configure mock to return our mock objects
        self.mock_initialize.return_value = (
            self.mocked_model,
            self.mocked_tokenizer,
            self.device
        )
        
        # Set module-level variables to our mocks
        from gregory.utils import summariser
        summariser._MODEL = self.mocked_model
        summariser._TOKENIZER = self.mocked_tokenizer
        summariser._DEVICE = self.device
    
    def tearDown(self):
        """Clean up after the test."""
        self.patcher.stop()
        
        # Reset module-level variables
        from gregory.utils import summariser
        summariser._MODEL = None
        summariser._TOKENIZER = None
        summariser._DEVICE = None
    
    def test_summarise_empty_string(self):
        """Test that summarise handles empty strings correctly."""
        result = summarise("")
        self.assertEqual(result, "")
    
    def test_summarise_non_empty_string(self):
        """Test that summarise works with non-empty strings."""
        result = summarise("This is a test.")
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)
    
    def test_summarise_bulk_empty_list(self):
        """Test that summarise_bulk handles empty lists correctly."""
        result = summarise_bulk([])
        self.assertEqual(result, [])
    
    def test_summarise_bulk_with_empty_strings(self):
        """Test that summarise_bulk handles lists with empty strings."""
        result = summarise_bulk(["", "Test", ""])
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], "")
        self.assertTrue(len(result[1]) > 0)
        self.assertEqual(result[2], "")
    
    def test_summarise_bulk_preserves_order(self):
        """Test that summarise_bulk preserves the order of inputs."""
        texts = ["First text", "Second text", "Third text"]
        results = summarise_bulk(texts)
        self.assertEqual(len(results), 3)
        
        # We can't check exact values since they're mocked,
        # but we can check that all are non-empty
        for result in results:
            self.assertIsInstance(result, str)
            self.assertTrue(len(result) > 0)
    
    def test_summarise_bulk_with_batch_size(self):
        """Test that summarise_bulk works with different batch sizes."""
        texts = ["Text 1", "Text 2", "Text 3", "Text 4", "Text 5"]
        
        # Try with batch size 2
        results_batch_2 = summarise_bulk(texts, batch_size=2)
        self.assertEqual(len(results_batch_2), 5)
        
        # Try with batch size 5
        results_batch_5 = summarise_bulk(texts, batch_size=5)
        self.assertEqual(len(results_batch_5), 5)
        
        # Try with batch size larger than input
        results_batch_10 = summarise_bulk(texts, batch_size=10)
        self.assertEqual(len(results_batch_10), 5)
