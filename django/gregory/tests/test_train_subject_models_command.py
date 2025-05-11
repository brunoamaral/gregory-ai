from django.test import TestCase
from django.core.management import call_command
from io import StringIO
from organizations.models import Organization
from gregory.models import Team, Subject, PredictionRunLog

class TrainSubjectModelsCommandTest(TestCase):
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
