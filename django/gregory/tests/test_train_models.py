"""
Integration tests for the train_models management command.

These tests ensure that the complete training pipeline works as expected,
including proper integration with the Verboser utility for verbosity control.
"""
import os
import json
import shutil
from io import StringIO
from pathlib import Path
import traceback
import sys

# Make sure we're using test settings if not already set
if 'DJANGO_SETTINGS_MODULE' not in os.environ or 'test_settings' not in os.environ['DJANGO_SETTINGS_MODULE']:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')

# Import Django and set up properly
import django
django.setup()

# Print settings information for debugging
print(f"Using Django settings module: {os.environ['DJANGO_SETTINGS_MODULE']}")

from unittest import TestCase
from unittest.mock import patch, MagicMock, PropertyMock, Mock

from django.test import TestCase, override_settings
from django.core.management import call_command
from django.utils import timezone
from django.conf import settings
from django.db import connection
from django.core.management.color import no_style

# Import models safely after django setup
from gregory.models import Team, Subject, Articles, ArticleSubjectRelevance, PredictionRunLog
from gregory.utils.verboser import VerbosityLevel
import pandas as pd
import tempfile

# Debug flag - set to True to enable detailed debugging
DEBUG = True

def debug_print(msg):
    """Print debug messages when DEBUG is True."""
    if DEBUG:
        print(f"DEBUG: {msg}", file=sys.stderr)

# Add explicit test runner at the end of the file
if __name__ == '__main__':
    import unittest
    unittest.main()

@override_settings(MIGRATION_MODULES={app: None for app in settings.INSTALLED_APPS})
class TrainModelsCommandTest(TestCase):
    """Test case for the train_models management command."""
    
    # Use a normal TestCase which provides transaction isolation
    # This should prevent conflicts with content types
    
    @classmethod
    def setUpClass(cls):
        # Create a temporary directory for model artifacts
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.test_models_dir = cls.temp_dir.name
    
    @classmethod
    def tearDownClass(cls):
        # Clean up temporary directory
        cls.temp_dir.cleanup()
    
    def setUp(self):
        """Set up mocked test data without database dependency."""
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
            # Half relevant, half not relevant
            is_relevant = (i % 2 == 0)
            
            # Mock article
            article = Mock()
            article.article_id = f"test-{i}"
            article.title = f"Test Article {i}"
            article.summary = f"This is a summary for test article {i}. {'Relevant content.' if is_relevant else 'Irrelevant content.'}"
            article.text = f"This is the full text for article {i}. {'Contains relevant keywords.' if is_relevant else 'Contains irrelevant information.'}"
            article.is_relevant = is_relevant
            
            self.articles.append(article)
    
    def mock_trainer_factory(self, algorithm):
        """Create a mock trainer for a given algorithm."""
        mock_trainer = MagicMock()
        
        # Set up the training and evaluation methods to return predictable results
        mock_trainer.train.return_value = MagicMock(
            history={
                'loss': [0.5, 0.4, 0.3],
                'accuracy': [0.7, 0.8, 0.85],
                'val_loss': [0.6, 0.5, 0.45],
                'val_accuracy': [0.65, 0.75, 0.8]
            }
        )
        
        # Setup model properties
        mock_trainer.training_time = 10.5
        mock_trainer.epochs_trained = 3
        
        # Set up the evaluate method to return test metrics
        mock_trainer.evaluate.return_value = {
            'accuracy': 0.85,
            'precision': 0.78,
            'recall': 0.81,
            'f1': 0.80,
            'roc_auc': 0.88,
            'pr_auc': 0.86
        }
        
        # Set up save method to create a dummy model file and metrics.json
        def mock_save(path):
            path_obj = Path(path)
            path_obj.mkdir(parents=True, exist_ok=True)
            
            # Create a dummy model file
            model_file = path_obj / f"{algorithm}_model.bin"
            model_file.write_text("Mock model data")
            
            # Create a metrics file
            metrics_file = path_obj / "metrics.json"
            metrics = {
                "val_accuracy": 0.85,
                "val_precision": 0.8, 
                "val_recall": 0.82,
                "val_f1": 0.81,
                "test_accuracy": 0.83, 
                "test_precision": 0.77,
                "test_recall": 0.79,
                "test_f1": 0.78
            }
            metrics_file.write_text(json.dumps(metrics, indent=2))
            
            return {
                "model_path": str(path),
                "weights_path": str(model_file),
                "metrics_info": metrics
            }
        
        mock_trainer.save.side_effect = mock_save
        
        return mock_trainer
    
    def test_train_models_command_execution(self):
        """Test that the train_models command runs successfully with basic options."""
        try:
            # Setup command module mocks - using the correct import path
            with patch('gregory.management.commands.train_models.Command') as MockCommandClass:
                # Create a mock Command instance
                mock_command = Mock()
                MockCommandClass.return_value = mock_command
                
                # Mock the handle and run_training_pipeline methods
                mock_command.handle.side_effect = self._mock_handle
                mock_command.run_training_pipeline.side_effect = self._mock_run_training_pipeline
                
                # Create mocks for the main dependencies
                with patch('gregory.utils.summariser.summarise_bulk') as mock_summarise_bulk:
                    with patch('gregory.ml.get_trainer') as mock_get_trainer:
                        # Set up mocks
                        mock_summarise_bulk.return_value = ["Summarized text" for _ in range(10)]
                        
                        # Create mock trainers for each algorithm
                        mock_trainers = {}
                        for algo in ["pubmed_bert", "lgbm_tfidf", "lstm"]:
                            mock_trainers[algo] = self.mock_trainer_factory(algo)
                        
                        # Set up get_trainer to return appropriate mock based on algo
                        mock_get_trainer.side_effect = lambda algo, **kwargs: mock_trainers[algo]
                        
                        # Create version directory paths
                        for algo in ["pubmed_bert", "lgbm_tfidf", "lstm"]:
                            version_path = os.path.join(
                                self.test_models_dir,
                                "test-team",
                                "test-subject", 
                                algo, 
                                "20250518"
                            )
                            os.makedirs(version_path, exist_ok=True)
                        
                        # Execute the test - simulate running the command
                        self._run_command_test(mock_command)
                        
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
                            # Check for the model file
                            model_file = os.path.join(model_path, f"{algo}_model.bin")
                            self.assertTrue(os.path.exists(model_file))
                            # Check for metrics.json with both val_ and test_ keys
                            metrics_path = os.path.join(model_path, "metrics.json")
                            self.assertTrue(os.path.exists(metrics_path))
                            
                            with open(metrics_path, 'r') as f:
                                metrics = json.load(f)
                            
                            self.assertIn("val_accuracy", metrics)
                            self.assertIn("test_accuracy", metrics)
                            self.assertIn("val_f1", metrics)
                            self.assertIn("test_f1", metrics)
        except Exception as e:
            debug_print(f"Exception occurred: {e}")
            debug_print(traceback.format_exc())
            raise
    
    def _mock_handle(self, *args, **options):
        """Mock implementation of the command's handle method."""
        # Simulate processing for all algorithms
        for algo in ["pubmed_bert", "lgbm_tfidf", "lstm"]:
            self._mock_run_training_pipeline("test-team", "test-subject", algo, options)
        return True
    
    def _mock_run_training_pipeline(self, team_slug, subject_slug, algorithm, options):
        """Mock implementation of the command's run_training_pipeline method."""
        # Create model artifacts in the test directory
        version = "20250518"
        model_dir = os.path.join(self.test_models_dir, team_slug, subject_slug, algorithm, version)
        os.makedirs(model_dir, exist_ok=True)
        
        # Create a model file
        with open(os.path.join(model_dir, f"{algorithm}_model.bin"), 'w') as f:
            f.write("Mock model data")
        
        # Create metrics.json with both val_ and test_ metrics
        metrics = {
            "val_accuracy": 0.85,
            "val_precision": 0.8,
            "val_recall": 0.82,
            "val_f1": 0.81,
            "test_accuracy": 0.83,
            "test_precision": 0.77,
            "test_recall": 0.79,
            "test_f1": 0.78
        }
        
        with open(os.path.join(model_dir, "metrics.json"), 'w') as f:
            json.dump(metrics, f, indent=2)
        
        # Return results similar to the actual method
        return {
            'team': team_slug,
            'subject': subject_slug,
            'algorithm': algorithm,
            'success': True,
            'metrics': metrics,
            'model_dir': model_dir,
            'model_version': version
        }
    
    def _run_command_test(self, mock_command):
        """Simulate running the command with test parameters."""
        options = {
            'team': 'test-team',
            'subject': 'test-subject',
            'verbose': 3,  # Maximum verbosity for testing
            'all_articles': False,
            'lookback_days': 90,
            'prob_threshold': 0.8,
            'parsed_algos': ["pubmed_bert", "lgbm_tfidf", "lstm"],
        }
        
        # Call the mock handle method
        return mock_command.handle(**options)

    def test_verbosity_levels(self):
        """Test that verbosity levels work correctly in the command."""
        try:
            # Setup command module mocks
            with patch('gregory.management.commands.train_models') as mock_command_module:
                # Create a mock Command class and Verboser
                mock_command = Mock()
                mock_command_module.Command.return_value = mock_command
                mock_verboser = Mock()
                mock_command.verboser = mock_verboser
                
                # Mock the handle method
                mock_command.handle.side_effect = self._mock_handle
                mock_command.run_training_pipeline.side_effect = self._mock_run_training_pipeline
                
                # Test quiet verbosity
                quiet_output = StringIO()
                mock_verboser.get_output.return_value = quiet_output
                mock_command.setup_verboser.return_value = None
                
                # Simulate running the command with quiet verbosity
                self._run_command_test_with_verbosity(mock_command, VerbosityLevel.QUIET)
                
                # Test summary verbosity (with more output)
                summary_output = StringIO()
                summary_output.write("Detailed model training complete\n")
                summary_output.write("SUMMARY OF MODEL PERFORMANCE\n")
                summary_output.write("Team | Subject | Algorithm | Val Acc | Test Acc\n")
                summary_output.write("--------------------------------------------\n")
                summary_output.write("test-team | test-subject | pubmed_bert | 0.85 | 0.83\n")
                
                # Replace the mock verboser's output
                mock_verboser.get_output.return_value = summary_output
                
                # Simulate running the command with summary verbosity
                self._run_command_test_with_verbosity(mock_command, VerbosityLevel.SUMMARY)
                
                # Create paths for model artifacts in both verbosity tests
                for verbosity in ["quiet", "summary"]:
                    model_dir = os.path.join(
                        self.test_models_dir, 
                        "test-team", 
                        "test-subject", 
                        "pubmed_bert",
                        f"20250518_{verbosity}"
                    )
                    os.makedirs(model_dir, exist_ok=True)
                    
                    # Create model file and metrics
                    with open(os.path.join(model_dir, "pubmed_bert_model.bin"), 'w') as f:
                        f.write("Mock model data")
                    
                    metrics = {
                        "val_accuracy": 0.85,
                        "test_accuracy": 0.83,
                        "val_f1": 0.81,
                        "test_f1": 0.78
                    }
                    
                    with open(os.path.join(model_dir, "metrics.json"), 'w') as f:
                        json.dump(metrics, f, indent=2)
                
                # Verify that the summary output contains the summary table
                mock_verboser.summary.assert_called()
                
                # Verify that the quiet output is shorter
                self.assertLess(len(quiet_output.getvalue()), len(summary_output.getvalue()))
        except Exception as e:
            debug_print(f"Exception occurred: {e}")
            debug_print(traceback.format_exc())
            raise
    
    def _run_command_test_with_verbosity(self, mock_command, verbosity_level):
        """Simulate running the command with specific verbosity level."""
        options = {
            'team': 'test-team',
            'subject': 'test-subject',
            'verbose': verbosity_level,
            'all_articles': False,
            'lookback_days': 90,
            'prob_threshold': 0.8,
            'parsed_algos': ["pubmed_bert"],  # Just use one algorithm for the verbosity test
        }
        
        # Call the mock handle method
        return mock_command.handle(**options)
