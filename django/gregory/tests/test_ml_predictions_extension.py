from django.test import TestCase
from django.db.utils import IntegrityError
from django.utils import timezone
from organizations.models import Organization
from gregory.models import Team, Subject, Articles, MLPredictions


class MLPredictionsExtensionTestCase(TestCase):
    def setUp(self):
        # Create a test organization and team
        self.org = Organization.objects.create(name="Test Org")
        self.team = Team.objects.create(organization=self.org, slug="test-org")
        
        # Create a test subject
        self.subject = Subject.objects.create(
            subject_name="Neurology",
            team=self.team,
            subject_slug="neurology"
        )
        
        # Create test articles
        self.article1 = Articles.objects.create(
            title="Test Article 1",
            link="https://example.com/article1",
            summary="This is a test article"
        )
        self.article1.subjects.add(self.subject)
        self.article1.teams.add(self.team)
        
        self.article2 = Articles.objects.create(
            title="Test Article 2",
            link="https://example.com/article2",
            summary="This is another test article"
        )
        self.article2.subjects.add(self.subject)
        self.article2.teams.add(self.team)
        
        # Create a test ML prediction
        self.ml_prediction = MLPredictions.objects.create(
            subject=self.subject,
            article=self.article1,
            model_version="v1.0.0",
            probability_score=0.85,
            predicted_relevant=True,
            gnb=True,
            lr=True,
            lsvc=False,
            mnb=True
        )
    
    def test_new_fields_exist(self):
        """Test that new fields exist and can be populated and retrieved"""
        # Fetch the prediction from the database
        prediction = MLPredictions.objects.get(id=self.ml_prediction.id)
        
        # Check article field
        self.assertIsNotNone(prediction.article)
        self.assertEqual(prediction.article, self.article1)
        
        # Check model_version field
        self.assertIsNotNone(prediction.model_version)
        self.assertEqual(prediction.model_version, "v1.0.0")
        
        # Check probability_score field
        self.assertIsNotNone(prediction.probability_score)
        self.assertEqual(prediction.probability_score, 0.85)
        
        # Check predicted_relevant field
        self.assertIsNotNone(prediction.predicted_relevant)
        self.assertTrue(prediction.predicted_relevant)
        
        # Check that legacy fields are still accessible
        self.assertTrue(prediction.gnb)
        self.assertTrue(prediction.lr)
        self.assertFalse(prediction.lsvc)
        self.assertTrue(prediction.mnb)
    
    def test_unique_constraint(self):
        """Test that the unique constraint is enforced"""
        from django.db import transaction
        
        # Try to create another prediction with the same article, subject, and model version
        try:
            with transaction.atomic():
                MLPredictions.objects.create(
                    subject=self.subject,
                    article=self.article1,
                    model_version="v1.0.0",  # Same as existing prediction
                    probability_score=0.75,
                    predicted_relevant=False
                )
            self.fail("IntegrityError was expected but not raised")
        except IntegrityError:
            # This is expected, the test passes
            pass
        
        # Check that we can create a prediction with a different article
        prediction2 = MLPredictions.objects.create(
            subject=self.subject,
            article=self.article2,
            model_version="v1.0.0",
            probability_score=0.65,
            predicted_relevant=False
        )
        self.assertIsNotNone(prediction2.id)
        
        # Check that we can create a prediction with a different model version
        prediction3 = MLPredictions.objects.create(
            subject=self.subject,
            article=self.article1,
            model_version="v1.1.0",  # Different version
            probability_score=0.90,
            predicted_relevant=True
        )
        self.assertIsNotNone(prediction3.id)
    
    def test_relations(self):
        """Test that relationships work in both directions"""
        # Check related_name on Article
        self.assertEqual(self.article1.ml_predictions_detail.count(), 1)
        self.assertEqual(self.article1.ml_predictions_detail.first(), self.ml_prediction)
        
        # Check related_name on Subject
        self.assertEqual(self.subject.ml_subject_predictions.count(), 1)
        self.assertEqual(self.subject.ml_subject_predictions.first(), self.ml_prediction)
        
        # Add another prediction and check counters
        MLPredictions.objects.create(
            subject=self.subject,
            article=self.article1,
            model_version="v2.0.0",
            probability_score=0.95,
            predicted_relevant=True
        )
        self.assertEqual(self.article1.ml_predictions_detail.count(), 2)
        self.assertEqual(self.subject.ml_subject_predictions.count(), 2)
    
    def test_null_handling(self):
        """Test how the model handles null values in the unique constraint"""
        # Create a prediction with null article - this should be allowed
        pred_null_article = MLPredictions.objects.create(
            subject=self.subject,
            article=None,  # Null article
            model_version="v1.0.0",
            probability_score=0.75,
            predicted_relevant=True
        )
        self.assertIsNotNone(pred_null_article.id)
        
        # Create another prediction with null article - should still be allowed 
        # since NULL != NULL in SQL's unique constraint rules
        pred_null_article2 = MLPredictions.objects.create(
            subject=self.subject,
            article=None,  # Null article
            model_version="v1.0.0",
            probability_score=0.80,
            predicted_relevant=True
        )
        self.assertIsNotNone(pred_null_article2.id)
        
        # Create a prediction with null model_version
        pred_null_version = MLPredictions.objects.create(
            subject=self.subject,
            article=self.article2,
            model_version=None,  # Null model version
            probability_score=0.85,
            predicted_relevant=True
        )
        self.assertIsNotNone(pred_null_version.id)
        
        # Another prediction with the same article, subject but null model_version
        # This should be allowed because NULL values are exempt from uniqueness checks
        pred_null_version2 = MLPredictions.objects.create(
            subject=self.subject,
            article=self.article2,
            model_version=None,  # Null model version
            probability_score=0.90,
            predicted_relevant=True
        )
        self.assertIsNotNone(pred_null_version2.id)
        
    def test_get_latest_prediction(self):
        """Test the get_latest_prediction helper method"""
        # First, let's create several predictions with different timestamps
        import time
        
        # Create a prediction with an older timestamp for version 3.0.0
        older_prediction = MLPredictions.objects.create(
            subject=self.subject,
            article=self.article1,
            model_version="v3.0.0",
            probability_score=0.65,
            predicted_relevant=False
        )
        
        # Brief delay to ensure different created_date
        time.sleep(0.05)
        
        # Create a newer prediction with a different version
        # Use v3.0.1 instead of v3.0.0 to avoid unique constraint violation
        newer_prediction = MLPredictions.objects.create(
            subject=self.subject,
            article=self.article1,
            model_version="v3.0.1",  # Using a different version to avoid unique constraint violation
            probability_score=0.95,
            predicted_relevant=True
        )
        
        # Test getting the latest prediction for a specific model version
        latest_v3 = MLPredictions.get_latest_prediction(self.article1, self.subject)
        self.assertEqual(latest_v3, newer_prediction)
        
        # Test getting the latest prediction for a specific model version
        latest_v1 = MLPredictions.get_latest_prediction(self.article1, self.subject, model_version="v1.0.0")
        self.assertEqual(latest_v1, self.ml_prediction)  # Our original prediction from setUp
        
        # Test getting the latest prediction across all model versions
        latest_any_version = MLPredictions.get_latest_prediction(self.article1, self.subject)
        self.assertEqual(latest_any_version, newer_prediction)  # Should be the newest one
        
        # Test getting prediction for non-existent article/subject
        non_existent = MLPredictions.get_latest_prediction(self.article2, self.subject, model_version="v4.0.0")
        self.assertIsNone(non_existent)
