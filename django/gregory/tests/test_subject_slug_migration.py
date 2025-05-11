from django.test import TestCase
from django.utils.text import slugify
from django.db.utils import IntegrityError
from gregory.models import Subject, Team
from organizations.models import Organization


class SubjectSlugMigrationTestCase(TestCase):
    def create_subject_with_slug(self, name, team):
        """Helper function to create subjects with proper slug handling"""
        # Check for existing slugs on this team
        base_slug = slugify(name)
        slug = base_slug
        counter = 1
        
        while Subject.objects.filter(team=team, subject_slug=slug).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
            
        return Subject.objects.create(
            subject_name=name,
            team=team,
            subject_slug=slug
        )
    
    def setUp(self):
        # Create test organizations and teams
        self.org1 = Organization.objects.create(name="Test Org 1")
        self.org2 = Organization.objects.create(name="Test Org 2")
        self.team1 = Team.objects.create(organization=self.org1, slug="test-org-1")
        self.team2 = Team.objects.create(organization=self.org2, slug="test-org-2")
        
        # Create test subjects
        self.subject1 = self.create_subject_with_slug("Neurology", self.team1)
        self.subject2 = self.create_subject_with_slug("Cardiology", self.team1)
        self.subject3 = self.create_subject_with_slug("Neurology", self.team2)  # Same name but different team
        
    def test_subject_slug_field_exists(self):
        """Test that the subject_slug field exists and is populated."""
        for subject in Subject.objects.all():
            self.assertIsNotNone(subject.subject_slug)
            self.assertNotEqual(subject.subject_slug, "")
            
    def test_slug_generation(self):
        """Test that slugs are generated correctly from subject names."""
        for subject in Subject.objects.all():
            base_slug = slugify(subject.subject_name)
            self.assertTrue(
                subject.subject_slug == base_slug or subject.subject_slug.startswith(f"{base_slug}-"),
                f"Subject slug '{subject.subject_slug}' doesn't match expected pattern for '{subject.subject_name}'"
            )
            
    def test_slug_uniqueness_per_team(self):
        """Test that slugs are unique per team but can repeat across teams."""
        # Verify we can have the same slug in different teams
        self.assertEqual(self.subject1.subject_slug, self.subject3.subject_slug)
        self.assertNotEqual(self.subject1.team, self.subject3.team)
        
        # Attempt to create a subject with a duplicate slug in the same team (should fail)
        with self.assertRaises(IntegrityError):
            Subject.objects.create(
                subject_name="Neurology Test",
                team=self.team1,
                subject_slug=self.subject1.subject_slug  # Same slug as subject1
            )
            
    def test_slug_collision_handling(self):
        """Test that slug collisions within a team are handled by adding suffixes."""
        # Create subjects with the same name in the same team
        subject1 = self.create_subject_with_slug("Test Subject", self.team1)
        self.assertEqual(subject1.subject_slug, "test-subject")
        
        subject2 = self.create_subject_with_slug("Test Subject", self.team1)
        self.assertEqual(subject2.subject_slug, "test-subject-1")
        
        subject3 = self.create_subject_with_slug("Test Subject", self.team1)
        self.assertEqual(subject3.subject_slug, "test-subject-2")
        
        # Verify that a subject with the same name in a different team gets the base slug
        subject4 = self.create_subject_with_slug("Test Subject", self.team2)
        self.assertEqual(subject4.subject_slug, "test-subject")
