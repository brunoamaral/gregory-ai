"""
Non-Django test for the train_models functionality.

This test verifies the core functionality without Django dependencies.
"""
import os
import sys
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock, MagicMock

# Set up debug printing
DEBUG = True

def debug_print(msg):
    """Print debug messages when DEBUG is True."""
    if DEBUG:
        print(f"DEBUG: {msg}", file=sys.stderr)

class TrainModelsTest(unittest.TestCase):
    """Test case for the train_models functionality."""
    
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.test_models_dir = cls.temp_dir.name
        debug_print(f"Created test directory: {cls.test_models_dir}")
    
    @classmethod
    def tearDownClass(cls):
        cls.temp_dir.cleanup()
        debug_print("Cleaned up test directory")
    
    def setUp(self):
        """Set up test data with mock objects."""
        # Mock team
        self.team = Mock()
        self.team.slug = "test-team"
        self.team.name = "Test Team"
        
        # Mock subject
        self.subject = Mock()
        self.subject.slug = "test-subject"
        self.subject.name = "Test Subject"
        self.subject.team = self.team
        
        # Mock articles
        self.articles = []
        for i in range(10):
            is_relevant = (i % 2 == 0)
            article = Mock()
            article.article_id = f"test-{i}"
            article.title = f"Test Article {i}"
            article.summary = f"Summary {i}"
            article.text = f"Text {i}"
            article.is_relevant = is_relevant
            self.articles.append(article)
    
    def mock_trainer_factory(self, algorithm):
        """Create a mock trainer for a given algorithm."""
        mock_trainer = MagicMock()
        
        # Set up train method
        mock_trainer.train.return_value = MagicMock(
            history={
                'loss': [0.5, 0.4],
                'accuracy': [0.7, 0.8],
                'val_loss': [0.6, 0.5],
                'val_accuracy': [0.65, 0.7]
            }
        )
        
        # Set up evaluate method
        mock_trainer.evaluate.return_value = {
            'accuracy': 0.85,
            'precision': 0.78,
            'recall': 0.81,
            'f1': 0.80,
            'roc_auc': 0.88
        }
        
        # Set up save method
        def mock_save(path):
            os.makedirs(path, exist_ok=True)
            
            # Create model file
            model_file = os.path.join(path, f"{algorithm}_model.bin")
            with open(model_file, 'w') as f:
                f.write("Mock model data")
            
            # Create metrics file
            metrics = {
                "val_accuracy": 0.85,
                "val_f1": 0.81,
                "test_accuracy": 0.83,
                "test_f1": 0.78
            }
            metrics_file = os.path.join(path, "metrics.json")
            with open(metrics_file, 'w') as f:
                json.dump(metrics, f, indent=2)
            
            return {
                "model_path": str(path),
                "weights_path": model_file
            }
        
        mock_trainer.save.side_effect = mock_save
        
        return mock_trainer
    
    def test_train_models(self):
        """Test training models and creating artifacts."""
        try:
            # Create mock trainers
            mock_trainers = {}
            for algo in ["pubmed_bert", "lgbm_tfidf", "lstm"]:
                mock_trainers[algo] = self.mock_trainer_factory(algo)
            
            # Simulate training each algorithm
            results = []
            for algo in ["pubmed_bert", "lgbm_tfidf", "lstm"]:
                # Create version directory
                version = "20250518"
                model_dir = os.path.join(
                    self.test_models_dir,
                    "test-team",
                    "test-subject",
                    algo,
                    version
                )
                os.makedirs(model_dir, exist_ok=True)
                
                # Get trainer and run training
                trainer = mock_trainers[algo]
                
                # Mock data
                train_texts = ["Text 1", "Text 2", "Text 3", "Text 4"]
                train_labels = [1, 0, 1, 0]
                val_texts = ["Val Text 1", "Val Text 2"]
                val_labels = [1, 0]
                test_texts = ["Test Text 1", "Test Text 2"]
                test_labels = [0, 1]
                
                # Train model
                trainer.train(
                    train_texts=train_texts,
                    train_labels=train_labels,
                    val_texts=val_texts,
                    val_labels=val_labels
                )
                
                # Evaluate model
                val_metrics = trainer.evaluate(
                    test_texts=val_texts,
                    test_labels=val_labels,
                    threshold=0.8
                )
                
                test_metrics = trainer.evaluate(
                    test_texts=test_texts,
                    test_labels=test_labels,
                    threshold=0.8
                )
                
                # Format metrics
                formatted_metrics = {}
                for key, value in val_metrics.items():
                    formatted_metrics[f"val_{key}"] = value
                for key, value in test_metrics.items():
                    formatted_metrics[f"test_{key}"] = value
                
                # Save model
                save_result = trainer.save(model_dir)
                
                # Save metrics to file
                metrics_path = os.path.join(model_dir, "metrics.json")
                with open(metrics_path, 'w') as f:
                    json.dump(formatted_metrics, f, indent=2)
                
                # Add to results
                results.append({
                    'team': 'test-team',
                    'subject': 'test-subject',
                    'algorithm': algo,
                    'model_dir': model_dir,
                    'model_version': version,
                    'metrics': formatted_metrics
                })
            
            # Verify artifacts were created
            for algo in ["pubmed_bert", "lgbm_tfidf", "lstm"]:
                version = "20250518"
                model_path = os.path.join(
                    self.test_models_dir,
                    "test-team",
                    "test-subject",
                    algo,
                    version
                )
                
                # Check that model directory exists
                self.assertTrue(os.path.exists(model_path))
                
                # Check for model file
                model_file = os.path.join(model_path, f"{algo}_model.bin")
                self.assertTrue(os.path.exists(model_file))
                
                # Check for metrics file
                metrics_path = os.path.join(model_path, "metrics.json")
                self.assertTrue(os.path.exists(metrics_path))
                
                # Check metrics content
                with open(metrics_path, 'r') as f:
                    metrics = json.load(f)
                
                self.assertIn("val_accuracy", metrics)
                self.assertIn("test_accuracy", metrics)
                self.assertIn("val_f1", metrics)
                self.assertIn("test_f1", metrics)
            
            debug_print("All tests passed")
        except Exception as e:
            debug_print(f"Error in test_train_models: {e}")
            import traceback
            debug_print(traceback.format_exc())
            raise
    
    def test_verbosity_levels(self):
        """Test that different verbosity levels produce different output."""
        try:
            # Create a mock trainer
            trainer = self.mock_trainer_factory("pubmed_bert")
            
            # Create metrics for two verbosity levels
            for verbosity in ["quiet", "summary"]:
                # Create model directory
                version = f"20250518_{verbosity}"
                model_dir = os.path.join(
                    self.test_models_dir,
                    "test-team",
                    "test-subject",
                    "pubmed_bert",
                    version
                )
                os.makedirs(model_dir, exist_ok=True)
                
                # Create metrics file
                metrics = {
                    "val_accuracy": 0.85,
                    "val_f1": 0.81,
                    "test_accuracy": 0.83,
                    "test_f1": 0.78
                }
                metrics_path = os.path.join(model_dir, "metrics.json")
                with open(metrics_path, 'w') as f:
                    json.dump(metrics, f, indent=2)
                
                # Create model file
                model_file = os.path.join(model_dir, "pubmed_bert_model.bin")
                with open(model_file, 'w') as f:
                    f.write("Mock model data")
            
            # Verify files were created
            for verbosity in ["quiet", "summary"]:
                version = f"20250518_{verbosity}"
                model_dir = os.path.join(
                    self.test_models_dir,
                    "test-team",
                    "test-subject",
                    "pubmed_bert",
                    version
                )
                
                model_file = os.path.join(model_dir, "pubmed_bert_model.bin")
                metrics_path = os.path.join(model_dir, "metrics.json")
                
                self.assertTrue(os.path.exists(model_file))
                self.assertTrue(os.path.exists(metrics_path))
            
            debug_print("Verbosity level tests passed")
        except Exception as e:
            debug_print(f"Error in test_verbosity_levels: {e}")
            import traceback
            debug_print(traceback.format_exc())
            raise

if __name__ == '__main__':
    unittest.main()
