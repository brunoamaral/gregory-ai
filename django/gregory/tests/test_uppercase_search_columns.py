from django.test import TestCase
from django.db import connection
from gregory.models import Articles, Trials
from django.contrib.auth.models import User
from organizations.models import Organization
from gregory.models import Team, Subject


class UppercaseSearchColumnsTestCase(TestCase):
    """
    Tests to verify that the uppercase search columns (utitle, usummary) 
    are working correctly and providing identical results to the old icontains queries.
    """
    
    def setUp(self):
        # Create test organization, team, and subject
        self.organization = Organization.objects.create(name="Test Org")
        self.team = Team.objects.create(name="Test Team", organization=self.organization, slug="test-team")
        self.subject = Subject.objects.create(subject_name="Test Subject", subject_slug="test-subject", team=self.team)
        
        # Create test articles
        self.article1 = Articles.objects.create(
            title="COVID-19 Research Study",
            summary="This is a comprehensive study about COVID-19 and its effects",
            link="http://example.com/1"
        )
        
        self.article2 = Articles.objects.create(
            title="Machine Learning in Healthcare",
            summary="Analysis of ML applications in medical research and covid treatment",
            link="http://example.com/2"
        )
        
        self.article3 = Articles.objects.create(
            title="Unrelated Research Topic",
            summary="This article is about something completely different",
            link="http://example.com/3"
        )
        
        # Create test trials
        self.trial1 = Trials.objects.create(
            title="COVID-19 Vaccine Trial",
            summary="Phase 3 trial for COVID-19 vaccine effectiveness",
            link="http://example.com/trial1"
        )
        
        self.trial2 = Trials.objects.create(
            title="Cancer Treatment Study", 
            summary="Novel approach to cancer therapy with covid considerations",
            link="http://example.com/trial2"
        )
        
        self.trial3 = Trials.objects.create(
            title="Diabetes Research",
            summary="Long-term diabetes management study",
            link="http://example.com/trial3"
        )
    
    def test_uppercase_columns_exist(self):
        """Test that the uppercase columns exist in the database"""
        with connection.cursor() as cursor:
            cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='articles' AND column_name IN ('utitle', 'usummary')")
            article_columns = [row[0] for row in cursor.fetchall()]
            self.assertIn('utitle', article_columns)
            self.assertIn('usummary', article_columns)
            
            cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name='trials' AND column_name IN ('utitle', 'usummary')")
            trial_columns = [row[0] for row in cursor.fetchall()]
            self.assertIn('utitle', trial_columns)
            self.assertIn('usummary', trial_columns)
    
    def test_uppercase_columns_populated(self):
        """Test that the uppercase columns are properly populated"""
        # Refresh from database to get computed column values
        article1 = Articles.objects.get(pk=self.article1.pk)
        self.assertEqual(article1.utitle, article1.title.upper())
        self.assertEqual(article1.usummary, article1.summary.upper())
        
        trial1 = Trials.objects.get(pk=self.trial1.pk)
        self.assertEqual(trial1.utitle, trial1.title.upper())
        self.assertEqual(trial1.usummary, trial1.summary.upper())
    
    def test_article_search_case_insensitive(self):
        """Test that uppercase column search produces same results as icontains"""
        search_term = "covid"
        
        # Old way (for comparison)
        old_results = Articles.objects.filter(
            title__icontains=search_term
        ).union(
            Articles.objects.filter(summary__icontains=search_term)
        )
        
        # New way using uppercase columns
        upper_term = search_term.upper()
        new_results = Articles.objects.filter(
            utitle__contains=upper_term
        ).union(
            Articles.objects.filter(usummary__contains=upper_term)
        )
        
        # Should return the same articles
        old_ids = set(old_results.values_list('article_id', flat=True))
        new_ids = set(new_results.values_list('article_id', flat=True))
        self.assertEqual(old_ids, new_ids)
        
        # Should find article1 (COVID in title) and article2 (covid in summary)
        expected_ids = {self.article1.article_id, self.article2.article_id}
        self.assertEqual(new_ids, expected_ids)
    
    def test_trial_search_case_insensitive(self):
        """Test that uppercase column search produces same results as icontains for trials"""
        search_term = "covid"
        
        # Old way (for comparison)
        old_results = Trials.objects.filter(
            title__icontains=search_term
        ).union(
            Trials.objects.filter(summary__icontains=search_term)
        )
        
        # New way using uppercase columns
        upper_term = search_term.upper()
        new_results = Trials.objects.filter(
            utitle__contains=upper_term
        ).union(
            Trials.objects.filter(usummary__contains=upper_term)
        )
        
        # Should return the same trials
        old_ids = set(old_results.values_list('trial_id', flat=True))
        new_ids = set(new_results.values_list('trial_id', flat=True))
        self.assertEqual(old_ids, new_ids)
        
        # Should find trial1 (COVID in title) and trial2 (covid in summary)
        expected_ids = {self.trial1.trial_id, self.trial2.trial_id}
        self.assertEqual(new_ids, expected_ids)
    
    def test_mixed_case_search(self):
        """Test search with mixed case terms"""
        search_terms = ["COVID", "Covid", "covid", "CoViD"]
        
        for term in search_terms:
            # All should return the same results
            upper_term = term.upper()
            results = Articles.objects.filter(
                utitle__contains=upper_term
            ).union(
                Articles.objects.filter(usummary__contains=upper_term)
            )
            
            result_ids = set(results.values_list('article_id', flat=True))
            expected_ids = {self.article1.article_id, self.article2.article_id}
            self.assertEqual(result_ids, expected_ids, f"Failed for search term: {term}")
    
    
    def test_gin_indexes_exist(self):
        """Test that GIN indexes exist on the uppercase columns"""
        with connection.cursor() as cursor:
            # Check for GIN indexes on articles table
            cursor.execute("""
                SELECT indexname 
                FROM pg_indexes 
                WHERE tablename = 'articles' 
                AND indexname IN ('articles_utitle_gin_idx', 'articles_usummary_gin_idx')
            """)
            article_indexes = [row[0] for row in cursor.fetchall()]
            self.assertIn('articles_utitle_gin_idx', article_indexes)
            self.assertIn('articles_usummary_gin_idx', article_indexes)
            
            # Check for GIN indexes on trials table
            cursor.execute("""
                SELECT indexname 
                FROM pg_indexes 
                WHERE tablename = 'trials' 
                AND indexname IN ('trials_utitle_gin_idx', 'trials_usummary_gin_idx')
            """)
            trial_indexes = [row[0] for row in cursor.fetchall()]
            self.assertIn('trials_utitle_gin_idx', trial_indexes)
            self.assertIn('trials_usummary_gin_idx', trial_indexes)

    def test_empty_search_handling(self):
        """Test that empty search terms are handled correctly"""
        # Empty string search should return no results
        results = Articles.objects.filter(utitle__contains="")
        self.assertEqual(results.count(), Articles.objects.count())  # contains empty string matches all
        
        # None/null handling
        results = Articles.objects.filter(utitle__contains="NONEXISTENT")
        self.assertEqual(results.count(), 0)
    
    def test_special_characters_search(self):
        """Test search with special characters"""
        # Create article with special characters
        special_article = Articles.objects.create(
            title="COVID-19: A Study (2023)",
            summary="Research on COVID-19's effects & implications",
            link="http://example.com/special"
        )
        
        # Search for terms with special characters
        search_results = Articles.objects.filter(utitle__contains="COVID-19")
        self.assertIn(special_article, search_results)
        
        search_results = Articles.objects.filter(usummary__contains="EFFECTS &")
        self.assertIn(special_article, search_results)
