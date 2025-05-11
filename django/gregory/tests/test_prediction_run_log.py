from django.test import TestCase
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from organizations.models import Organization
from gregory.models import Team, Subject, PredictionRunLog
import datetime


class PredictionRunLogTestCase(TestCase):
    def setUp(self):
        # Create test organization and team
        self.org = Organization.objects.create(name="Test Org")
        self.team = Team.objects.create(organization=self.org, slug="test-org")
        
        # Create test subjects
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
        
        # Create some test log entries
        self.train_log = PredictionRunLog.objects.create(
            team=self.team,
            subject=self.subject1,
            model_version="v1.0.0",
            run_type="train",
            triggered_by="test_user",
            run_started=timezone.now() - datetime.timedelta(hours=2),
            run_finished=timezone.now() - datetime.timedelta(hours=1, minutes=30),
            success=True
        )
        
        self.predict_log = PredictionRunLog.objects.create(
            team=self.team,
            subject=self.subject1,
            model_version="v1.0.0",
            run_type="predict",
            triggered_by="test_user",
            run_started=timezone.now() - datetime.timedelta(hours=1),
            run_finished=timezone.now() - datetime.timedelta(minutes=45),
            success=True
        )
        
        self.failed_log = PredictionRunLog.objects.create(
            team=self.team,
            subject=self.subject1,
            model_version="v1.0.0",
            run_type="predict",
            triggered_by="test_user",
            run_started=timezone.now() - datetime.timedelta(minutes=30),
            run_finished=timezone.now() - datetime.timedelta(minutes=15),
            success=False,
            error_message="Model failed to predict due to missing features"
        )
        
        # Create an ongoing run (no run_finished)
        self.ongoing_log = PredictionRunLog.objects.create(
            team=self.team,
            subject=self.subject2,
            model_version="v1.0.0",
            run_type="train",
            triggered_by="test_user",
            success=None  # None means still running
        )
    
    def test_create_log_entry(self):
        """Test that we can create valid log entries"""
        log_count = PredictionRunLog.objects.count()
        self.assertEqual(log_count, 4)  # The 4 logs created in setUp
        
        # Create a new log entry
        new_log = PredictionRunLog.objects.create(
            team=self.team,
            subject=self.subject2,
            model_version="v1.0.1",
            run_type="predict",
            triggered_by="api_user"
        )
        
        self.assertIsNotNone(new_log.id)
        self.assertEqual(new_log.run_type, "predict")
        self.assertIsNone(new_log.run_finished)
        self.assertIsNone(new_log.success)
    
    def test_run_type_constraint(self):
        """Test that run_type enforces the choices constraint"""
        with self.assertRaises(ValidationError):
            invalid_log = PredictionRunLog(
                team=self.team,
                subject=self.subject1,
                model_version="v1.0.0",
                run_type="invalid_type",  # Invalid choice
                triggered_by="test_user"
            )
            # Force validation of the field
            invalid_log.full_clean()
    
    def test_get_latest_run(self):
        """Test the get_latest_run class method"""
        # Get latest run for subject1 (any type)
        latest = PredictionRunLog.get_latest_run(self.team, self.subject1)
        self.assertEqual(latest, self.failed_log)  # Latest completed run
        
        # Get latest training run for subject1
        latest_train = PredictionRunLog.get_latest_run(self.team, self.subject1, run_type="train")
        self.assertEqual(latest_train, self.train_log)
        
        # Get latest prediction run for subject1
        latest_predict = PredictionRunLog.get_latest_run(self.team, self.subject1, run_type="predict")
        self.assertEqual(latest_predict, self.failed_log)  # Latest is the failed one
        
        # For subject2, the only run is ongoing, so get_latest_run should return None
        latest_subject2 = PredictionRunLog.get_latest_run(self.team, self.subject2)
        self.assertIsNone(latest_subject2)  # No completed runs
        
        # Complete the ongoing run
        self.ongoing_log.run_finished = timezone.now()
        self.ongoing_log.success = True
        self.ongoing_log.save()
        
        # Now we should get it as the latest run
        latest_subject2_after = PredictionRunLog.get_latest_run(self.team, self.subject2)
        self.assertEqual(latest_subject2_after, self.ongoing_log)
    
    def test_str_representation(self):
        """Test the string representation of the model"""
        self.assertEqual(
            str(self.train_log),
            f"Training run for {self.team} - {self.subject1} (Successful)"
        )
        self.assertEqual(
            str(self.failed_log),
            f"Prediction run for {self.team} - {self.subject1} (Failed)"
        )
        self.assertEqual(
            str(self.ongoing_log),
            f"Training run for {self.team} - {self.subject2} (Running)"
        )
