"""
Tests for the dataset module.
"""
import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'admin.settings')
django.setup()

from datetime import datetime, timedelta
from unittest import mock
import pandas as pd
from django.test import TestCase

from gregory.utils.dataset import collect_articles, build_dataset, train_val_test_split
from gregory.models import Team, Subject, Articles, ArticleSubjectRelevance
from organizations.models import Organization  # Import Organization model


class DatasetTestCase(TestCase):
    """Test case for the dataset utils."""
    
    def setUp(self):
        """Set up test data."""
        # Create an organization first
        self.organization = Organization.objects.create(name='Test Organization')
        
        # Create a team and subject
        self.team = Team.objects.create(
            slug='test-team',
            name='Test Team',
            organization=self.organization
        )
        
        self.subject = Subject.objects.create(
            subject_name='Test Subject',
            subject_slug='test-subject',
            team=self.team
        )
        
        # Create sample articles
        self.articles = []
        for i in range(5):
            article = Articles.objects.create(
                title=f'Test Article {i}',
                link=f'https://example.com/{i}',
                discovery_date=datetime.now() - timedelta(days=i)
            )
            article.teams.add(self.team)
            article.subjects.add(self.subject)
            self.articles.append(article)
            
            # Create relevance relationship (alternating relevant/not relevant)
            ArticleSubjectRelevance.objects.create(
                article=article,
                subject=self.subject,
                is_relevant=(i % 2 == 0)  # Even indices are relevant
            )
    
    def test_collect_articles_returns_queryset(self):
        """Test that collect_articles returns the correct QuerySet."""
        articles = collect_articles('test-team', 'test-subject')
        self.assertEqual(articles.count(), 5)
        
    def test_collect_articles_with_window(self):
        """Test that windowed collection returns the correct subset."""
        # Force the discovery dates to be further apart to ensure proper filtering
        # Set the first 3 articles to be within the last 2 days
        for i in range(3):
            self.articles[i].discovery_date = datetime.now() - timedelta(hours=i)
            self.articles[i].save()
        
        # Set the remaining articles to be older than 2 days
        for i in range(3, 5):
            self.articles[i].discovery_date = datetime.now() - timedelta(days=3, hours=i)
            self.articles[i].save()
            
        # Now test the window filtering
        articles = collect_articles('test-team', 'test-subject', window_days=2)
        self.assertEqual(articles.count(), 3)  # Only articles from last 2 days
    
    def test_collect_articles_handles_invalid_team(self):
        """Test that collect_articles raises exception for invalid team."""
        with self.assertRaises(Team.DoesNotExist):
            collect_articles('nonexistent-team', 'test-subject')
    
    def test_collect_articles_handles_invalid_subject(self):
        """Test that collect_articles raises exception for invalid subject."""
        with self.assertRaises(Subject.DoesNotExist):
            collect_articles('test-team', 'nonexistent-subject')
    
    def test_build_dataset_creates_dataframe(self):
        """Test that build_dataset returns a DataFrame with the right structure."""
        articles = collect_articles('test-team', 'test-subject')
        df = build_dataset(articles)
        
        # Check DataFrame structure
        self.assertIsInstance(df, pd.DataFrame)
        self.assertIn('article_id', df.columns)
        self.assertIn('text', df.columns)
        self.assertIn('relevant', df.columns)
        
        # Check DataFrame size
        self.assertEqual(len(df), 5)
        
        # Check relevance values
        relevant_count = df['relevant'].sum()
        self.assertEqual(relevant_count, 3)  # 3 articles should be relevant (indices 0, 2, 4)
    
    def test_train_val_test_split_correct_sizes(self):
        """Test that train_val_test_split returns the expected proportions."""
        # Create a synthetic dataset with an even split of relevant/not relevant
        data = []
        for i in range(100):
            data.append({
                'article_id': i,
                'text': f'This is article {i}',
                'relevant': i % 2  # 50 relevant, 50 not relevant
            })
        df = pd.DataFrame(data)
        
        # Split the dataset
        train_df, val_df, test_df = train_val_test_split(df)
        
        # Check that proportions are correct (allow small rounding errors)
        self.assertAlmostEqual(len(train_df) / len(df), 0.7, delta=0.02)
        self.assertAlmostEqual(len(val_df) / len(df), 0.15, delta=0.02)
        self.assertAlmostEqual(len(test_df) / len(df), 0.15, delta=0.02)
        
        # Check that all data points are accounted for
        self.assertEqual(len(train_df) + len(val_df) + len(test_df), len(df))
        
    def test_train_val_test_split_stratification(self):
        """Test that stratification maintains class balance across splits."""
        # Create a synthetic dataset with an even split of relevant/not relevant
        data = []
        for i in range(100):
            data.append({
                'article_id': i,
                'text': f'This is article {i}',
                'relevant': i % 2  # 50 relevant, 50 not relevant
            })
        df = pd.DataFrame(data)
        
        # Split the dataset
        train_df, val_df, test_df = train_val_test_split(df)
        
        # Check stratification in each split (proportion of relevant should be ~0.5)
        self.assertAlmostEqual(train_df['relevant'].mean(), 0.5, delta=0.05)
        self.assertAlmostEqual(val_df['relevant'].mean(), 0.5, delta=0.05)
        self.assertAlmostEqual(test_df['relevant'].mean(), 0.5, delta=0.05)
    
    def test_train_val_test_split_custom_proportions(self):
        """Test train_val_test_split with custom split proportions."""
        # Create a synthetic dataset
        data = []
        for i in range(100):
            data.append({
                'article_id': i,
                'text': f'This is article {i}',
                'relevant': i % 2  # 50 relevant, 50 not relevant
            })
        df = pd.DataFrame(data)
        
        # Custom split proportions
        train_df, val_df, test_df = train_val_test_split(
            df, train_size=0.6, val_size=0.2, test_size=0.2
        )
        
        # Check that proportions are correct (allow small rounding errors)
        self.assertAlmostEqual(len(train_df) / len(df), 0.6, delta=0.02)
        self.assertAlmostEqual(len(val_df) / len(df), 0.2, delta=0.02)
        self.assertAlmostEqual(len(test_df) / len(df), 0.2, delta=0.02)
    
    def test_train_val_test_split_invalid_proportions(self):
        """Test that invalid proportions raise ValueError."""
        # Create a synthetic dataset
        data = []
        for i in range(100):
            data.append({
                'article_id': i,
                'text': f'This is article {i}',
                'relevant': i % 2
            })
        df = pd.DataFrame(data)
        
        # Proportions that don't sum to 1
        with self.assertRaises(ValueError):
            train_val_test_split(df, train_size=0.8, val_size=0.1, test_size=0.2)
    
    def test_train_val_test_split_stratification_failure(self):
        """Test that stratification raises ValueError when a class has only one value."""
        # Create a dataset where all examples have the same label
        data = []
        for i in range(10):
            data.append({
                'article_id': i,
                'text': f'This is article {i}',
                'relevant': 1  # All relevant
            })
        df = pd.DataFrame(data)
        
        # Should raise ValueError because stratification isn't possible
        with self.assertRaises(ValueError):
            train_val_test_split(df)
