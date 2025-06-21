from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from django.contrib.sites.models import Site
from gregory.models import Subject, Articles, Team, TeamCategory
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import Lists, Subscribers
from subscriptions.management.commands.utils.subscription import get_latest_research_by_category
from templates.emails.components.content_organizer import EmailContentOrganizer

class TestLatestResearchCategories(TestCase):
    def setUp(self):
        # Create test data
        self.organization = Organization.objects.create(name="Test Organization", slug="test-org")
        self.team = Team.objects.create(name="Test Team", organization=self.organization, slug="test-team")
        
        # Create subjects
        self.subject1 = Subject.objects.create(subject_name="Subject 1", team=self.team, subject_slug="subject-1")
        self.subject2 = Subject.objects.create(subject_name="Subject 2", team=self.team, subject_slug="subject-2")
        self.subject3 = Subject.objects.create(subject_name="Subject 3", team=self.team, subject_slug="subject-3")
        
        # Create team categories
        self.category1 = TeamCategory.objects.create(
            team=self.team,
            category_name="Category 1",
            category_slug="category-1"
        )
        self.category1.subjects.add(self.subject1)
        
        self.category2 = TeamCategory.objects.create(
            team=self.team,
            category_name="Category 2",
            category_slug="category-2"
        )
        self.category2.subjects.add(self.subject2, self.subject3)
        
        # Create list with latest research categories
        self.test_list = Lists.objects.create(
            list_name="Test List",
            weekly_digest=True,
            team=self.team
        )
        self.test_list.subjects.add(self.subject1, self.subject2)
        self.test_list.latest_research_categories.add(self.category1, self.category2)
        
        # Create empty list
        self.empty_list = Lists.objects.create(
            list_name="Empty List",
            weekly_digest=True,
            team=self.team
        )
        
        # Create articles
        self.article1 = Articles.objects.create(
            title="Article 1",
            discovery_date=timezone.now() - timedelta(days=5),
            doi="10.1234/article1"
        )
        self.article1.subjects.add(self.subject1)
        
        self.article2 = Articles.objects.create(
            title="Article 2",
            discovery_date=timezone.now() - timedelta(days=3),
            doi="10.1234/article2"
        )
        self.article2.subjects.add(self.subject1)
        
        self.article3 = Articles.objects.create(
            title="Article 3",
            discovery_date=timezone.now() - timedelta(days=1),
            doi="10.1234/article3"
        )
        self.article3.subjects.add(self.subject2)
        
        self.article4 = Articles.objects.create(
            title="Article 4",
            discovery_date=timezone.now() - timedelta(days=2),
            doi="10.1234/article4"
        )
        self.article4.subjects.add(self.subject3)

    def test_get_latest_research_by_category(self):
        # Test the get_latest_research_by_category function
        result = get_latest_research_by_category(self.test_list)
        
        # Verify results
        self.assertEqual(len(result), 2)  # Should have 2 categories
        self.assertIn(self.category1, result)
        self.assertIn(self.category2, result)
        
        # Verify article counts
        self.assertEqual(len(result[self.category1]), 2)  # Should have 2 articles from subject1
        self.assertEqual(len(result[self.category2]), 2)  # Should have 2 articles from subject2 and subject3
        
        # Test the organizer
        organizer = EmailContentOrganizer('weekly_summary')
        organized = organizer.organize_latest_research_by_category(result)
        
        # Verify organized results
        self.assertTrue(organized['has_latest_research'])
        self.assertEqual(organized['total_categories'], 2)
        self.assertEqual(organized['total_articles'], 4)
        
        # Test with empty list
        empty_result = get_latest_research_by_category(self.empty_list)
        self.assertEqual(len(empty_result), 0)
        
        empty_organized = organizer.organize_latest_research_by_category(empty_result)
        self.assertFalse(empty_organized['has_latest_research'])
        
    def test_maximum_articles_per_category(self):
        """Test that each category is limited to 20 articles maximum"""
        # Create 25 more articles for subject1 (category1)
        for i in range(5, 30):
            article = Articles.objects.create(
                title=f"Article {i}",
                discovery_date=timezone.now() - timedelta(days=i % 10),
                doi=f"10.1234/article{i}"
            )
            article.subjects.add(self.subject1)
        
        # Now category1 should have 27 articles total (2 existing + 25 new)
        result = get_latest_research_by_category(self.test_list)
        
        # Verify that only 20 articles are returned for category1
        self.assertEqual(len(result[self.category1]), 20)
        
        # Verify that the returned articles are the 20 most recent ones
        articles = list(result[self.category1])
        for i in range(len(articles) - 1):
            self.assertTrue(
                articles[i].discovery_date >= articles[i + 1].discovery_date,
                "Articles should be ordered by discovery_date (newest first)"
            )
