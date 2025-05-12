from django.test import TestCase
from django.core.management import call_command
from io import StringIO
from organizations.models import Organization
from gregory.models import Team, Subject, PredictionRunLog

class TrainSubjectModelsCommandTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Set up test data
        cls.org = Organization.objects.create(name="Test Organization")
        cls.team = Team.objects.create(organization=cls.org, slug="test-team")
        cls.subject = Subject.objects.create(
            subject_name="Test Subject",
            team=cls.team,
            subject_slug="test-subject"
        )

    def setUp(self):
        # Create test data
        self.org = Organization.objects.create(name="Test Organization")
        self.team = Team.objects.create(organization=self.org, slug="test-team")
        self.subject1 = Subject.objects.create(
            subject_name="Neurology",
            team=self.team,
            subject_slug="neurology"
        )
        self.subject2 = Subject.objects.create(
            subject_name="Cardiology",
            team=self.team,
            subject_slug="cardiology"
        )

    def test_dry_run(self):
        """Test that the command runs without errors in dry run mode"""
        # Set up StringIO to capture command output
        out = StringIO()
        
        # Call command with dry-run parameter
        call_command(
            'train_subject_models',
            '--dry-run',
            '--verbose',
            stdout=out
        )
        
        # Get command output
        output = out.getvalue()
        
        # Assert command output contains expected information
        self.assertIn("Running with options:", output)
        self.assertIn("Dry run: True", output)
        self.assertIn("Team: All teams", output)
        self.assertIn("Subject: All subjects", output)
        self.assertIn("Timeframe: 90 days", output)
        self.assertIn("Device: cpu", output)
        self.assertIn("Dry run completed. No models were trained.", output)
        
        # Ensure no PredictionRunLog entries were created
        self.assertEqual(PredictionRunLog.objects.count(), 0)
        
    def test_team_filter(self):
        """Test the command with team filter in dry run mode"""
        out = StringIO()
        
        call_command(
            'train_subject_models',
            '--team', 'test-team',
            '--dry-run',
            '--verbose',
            stdout=out
        )
        
        output = out.getvalue()
        
        self.assertIn("Team: test-team", output)
        self.assertIn("Processing team: Test Organization", output)
        self.assertEqual(PredictionRunLog.objects.count(), 0)
        
    def test_subject_filter(self):
        """Test the command with subject filter in dry run mode"""
        out = StringIO()
        
        call_command(
            'train_subject_models',
            '--team', 'test-team',
            '--subject', 'neurology',
            '--dry-run',
            '--verbose',
            stdout=out
        )
        
        output = out.getvalue()
        
        self.assertIn("Team: test-team", output)
        self.assertIn("Subject: neurology", output)
        self.assertIn("Filtered to subject: Neurology", output)
        self.assertEqual(PredictionRunLog.objects.count(), 0)
        
    def test_timeframe_and_device(self):
        """Test the command with custom timeframe and device parameters"""
        out = StringIO()
        
        call_command(
            'train_subject_models',
            '--timeframe', '30',
            '--device', 'gpu',
            '--dry-run',
            '--verbose',
            stdout=out
        )
        
        output = out.getvalue()
        
        self.assertIn("Timeframe: 30 days", output)
        self.assertIn("Device: gpu", output)
        
    def test_invalid_team(self):
        """Test the command handles invalid team gracefully"""
        out = StringIO()
        
        with self.assertRaises(Exception) as context:
            call_command(
                'train_subject_models',
                '--team', 'nonexistent-team',
                '--dry-run',
                stdout=out
            )
            
        # Check that the error message mentions the team slug
        self.assertIn("nonexistent-team", str(context.exception))
            
    def test_invalid_subject(self):
        """Test the command handles invalid subject gracefully"""
        out = StringIO()
        
        # This should not raise an exception, just log a warning
        call_command(
            'train_subject_models',
            '--team', 'test-team',
            '--subject', 'nonexistent-subject',
            '--dry-run',
            '--verbose',
            stdout=out
        )
        
        output = out.getvalue()
        self.assertIn("Subject with slug 'nonexistent-subject' does not exist", output)

    def test_model_loading_feature(self):
        """Test the new model loading functionality."""
        from unittest.mock import patch, MagicMock, mock_open
        import os
        import json
        
        # Mock the file operations and model loading
        with patch('os.path.exists', return_value=True), \
             patch('builtins.open', mock_open(read_data='{"model_type": "LogisticRegression_TFIDF"}')), \
             patch('gregory.management.commands.train_subject_models.joblib.load') as mock_joblib_load:
            
            # Set up the command
            from gregory.management.commands.train_subject_models import Command
            command = Command()
            command.verbosity = 1
            
            # Mock the loaded model objects
            mock_vectorizer = MagicMock()
            mock_model = MagicMock()
            mock_joblib_load.side_effect = [mock_vectorizer, mock_model]
            
            # Call load_model
            model, metadata = command.load_model('logreg', 'test-team', 'test-subject', 'v1.0.0')
            
            # Check that the model tuple is returned correctly
            self.assertEqual(len(model), 2)
            self.assertEqual(model[0], mock_vectorizer)
            self.assertEqual(model[1], mock_model)
            self.assertEqual(metadata['model_type'], 'LogisticRegression_TFIDF')
    
    def test_prediction_feature(self):
        """Test the prediction functionality."""
        from unittest.mock import patch, MagicMock
        
        # Mock the models and prediction
        with patch('gregory.management.commands.train_subject_models.Command.load_model') as mock_load_model, \
             patch('gregory.management.commands.train_subject_models.Command.predict') as mock_predict:
            
            # Set up mocks
            mock_model = MagicMock()
            mock_metadata = {'model_type': 'LogisticRegression_TFIDF'}
            mock_load_model.return_value = (mock_model, mock_metadata)
            
            mock_predict.return_value = {
                'predictions': [1, 0],
                'probabilities': [0.9, 0.2]
            }
            
            # Call the command with predict subcommand
            out = StringIO()
            call_command(
                'train_subject_models',
                'predict',
                team='test-team',
                subject='test-subject',
                model_type='logreg',
                input_text='This is a test article for prediction.',
                verbosity=1,
                stdout=out
            )
            
            # Verify command ran successfully
            output = out.getvalue()
            self.assertIn("Prediction completed successfully", output)
