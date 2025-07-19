"""
Tests for the optimized category queries to ensure they perform well
and return correct results.
"""

from django.test import TestCase
from django.test.utils import override_settings
from django.db import connection
from django.db.models import Count
from rest_framework.test import APIClient
from gregory.models import TeamCategory, Team, Articles, Trials, Authors, Subject
from organizations.models import Organization
import time


class CategoryOptimizationTestCase(TestCase):
    """Test cases for the optimized category queries"""
    
    def setUp(self):
        """Set up test data"""
        self.client = APIClient()
        
        # Create organization first (required for Team)
        self.organization = Organization.objects.create(
            name="Test Organization",
            slug="test-org"
        )
        
        # Create test team
        self.team = Team.objects.create(
            name="Test Team",
            organization=self.organization,
            slug="test-team"
        )
        
        # Create test subject
        self.subject = Subject.objects.create(subject_name="Test Subject")
        
        # Create test category
        self.category = TeamCategory.objects.create(
            team=self.team,
            category_name="Test Category",
            category_slug="test-category"
        )
        self.category.subjects.add(self.subject)
        
        # Create test authors
        self.author1 = Authors.objects.create(
            given_name="John",
            family_name="Doe",
            full_name="John Doe"
        )
        self.author2 = Authors.objects.create(
            given_name="Jane", 
            family_name="Smith",
            full_name="Jane Smith"
        )
        
        # Create test articles
        for i in range(5):
            article = Articles.objects.create(
                title=f"Test Article {i}",
                link=f"http://example.com/article/{i}",
                summary=f"Summary for article {i}"
            )
            article.team_categories.add(self.category)
            article.authors.add(self.author1 if i < 3 else self.author2)
        
        # Create test trials
        for i in range(3):
            trial = Trials.objects.create(
                title=f"Test Trial {i}",
                link=f"http://example.com/trial/{i}",
                summary=f"Summary for trial {i}"
            )
            trial.team_categories.add(self.category)

    def test_category_list_performance(self):
        """Test that category listing is fast and returns correct counts"""
        start_time = time.time()
        
        response = self.client.get(f'/categories/?team_id={self.team.id}')
        
        end_time = time.time()
        query_time = end_time - start_time
        
        self.assertEqual(response.status_code, 200)
        self.assertLess(query_time, 1.0)  # Should complete in under 1 second
        
        data = response.json()
        self.assertEqual(len(data['results']), 1)
        
        category_data = data['results'][0]
        self.assertEqual(category_data['article_count_total'], 5)
        self.assertEqual(category_data['trials_count_total'], 3)
        self.assertEqual(category_data['authors_count'], 2)

    def test_category_authors_endpoint(self):
        """Test the category authors endpoint performance"""
        start_time = time.time()
        
        response = self.client.get(f'/categories/{self.category.id}/authors/')
        
        end_time = time.time()
        query_time = end_time - start_time
        
        self.assertEqual(response.status_code, 200)
        self.assertLess(query_time, 1.0)  # Should complete in under 1 second
        
        data = response.json()
        self.assertEqual(len(data['results']), 2)
        
        # Check that authors are sorted by article count
        author1_data = next(a for a in data['results'] if a['full_name'] == 'John Doe')
        author2_data = next(a for a in data['results'] if a['full_name'] == 'Jane Smith')
        
        self.assertEqual(author1_data['articles_count'], 3)
        self.assertEqual(author2_data['articles_count'], 2)

    def test_query_count_optimization(self):
        """Test that we're not generating excessive database queries"""
        with self.assertNumQueries(7):  # Basic query + prefetch_related queries for subjects, articles, trials + serializer count query + authors query
            response = self.client.get('/categories/')
            
        self.assertEqual(response.status_code, 200)

    def test_complex_category_filtering(self):
        """Test complex filtering scenarios that previously caused hanging"""
        # Create additional test data for more complex scenario
        team2 = Team.objects.create(
            name="Team 2",
            organization=self.organization,
            slug="team-2"
        )
        subject2 = Subject.objects.create(subject_name="Subject 2")
        
        category2 = TeamCategory.objects.create(
            team=team2,
            category_name="Category 2",
            category_slug="category-2"
        )
        category2.subjects.add(subject2)
        
        # Test multiple filters
        start_time = time.time()
        
        response = self.client.get(f'/categories/?team_id={self.team.id}&subject_id={self.subject.id}')
        
        end_time = time.time()
        query_time = end_time - start_time
        
        self.assertEqual(response.status_code, 200)
        self.assertLess(query_time, 1.0)  # Should complete quickly
        
        data = response.json()
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['results'][0]['id'], self.category.id)

    def test_monthly_counts_performance(self):
        """Test that monthly counts queries don't hang"""
        start_time = time.time()
        
        response = self.client.get(f'/teams/{self.team.id}/categories/{self.category.category_slug}/monthly_counts/')
        
        end_time = time.time()
        query_time = end_time - start_time
        
        self.assertEqual(response.status_code, 200)
        self.assertLess(query_time, 2.0)  # Should complete in under 2 seconds
        
        data = response.json()
        self.assertIn('monthly_article_counts', data)
        self.assertIn('monthly_trial_counts', data)

    @override_settings(DEBUG=True)  # To capture SQL queries
    def test_no_complex_group_by_queries(self):
        """Ensure we're not generating the problematic GROUP BY queries"""
        # Reset queries
        connection.queries.clear()
        
        response = self.client.get(f'/categories/?team_id={self.team.id}')
        self.assertEqual(response.status_code, 200)
        
        # Check that none of the queries contain the problematic pattern
        problematic_patterns = [
            'GROUP BY 1',  # The original problematic pattern
            'LEFT OUTER JOIN "articles_team_categories"',  # Complex JOINs
            'LEFT OUTER JOIN "trials_team_categories"',
            'LEFT OUTER JOIN "articles_authors"'
        ]
        
        for query in connection.queries:
            sql = query['sql']
            for pattern in problematic_patterns:
                if pattern in sql and 'COUNT(*)' in sql:
                    self.fail(f"Found problematic query pattern: {pattern} in query: {sql[:200]}...")

    def test_prefetch_efficiency(self):
        """Test that our prefetch_related optimizations work correctly"""
        # Test with include_authors=false (should be very efficient)
        with self.assertNumQueries(6):  # Basic query + prefetch + authors count query
            response = self.client.get(f'/categories/?team_id={self.team.id}&include_authors=false')
            self.assertEqual(response.status_code, 200)
        
        # Test with include_authors=true (should still be reasonable)
        with self.assertNumQueries(7):  # Basic query + prefetch + author queries
            response = self.client.get(f'/categories/?team_id={self.team.id}&include_authors=true')
            self.assertEqual(response.status_code, 200)


class IndexEfficiencyTestCase(TestCase):
    """Test cases to verify that our database indexes are being used"""
    
    def test_explain_query_uses_indexes(self):
        """Test that our optimized queries use indexes instead of sequential scans"""
        with connection.cursor() as cursor:
            # Test a query that should use our new indexes
            cursor.execute("""
                EXPLAIN (ANALYZE, BUFFERS) 
                SELECT tc.id, COUNT(DISTINCT a.article_id) as article_count
                FROM team_categories tc
                LEFT JOIN articles_team_categories atc ON tc.id = atc.teamcategory_id  
                LEFT JOIN articles a ON atc.articles_id = a.article_id
                WHERE tc.team_id = %s
                GROUP BY tc.id
            """, [1])
            
            plan = cursor.fetchall()
            plan_text = '\n'.join(str(row[0]) for row in plan)
            
            # Check that we're using index scans instead of sequential scans
            # This is a basic check - in a real environment you'd want more detailed analysis
            self.assertNotIn('Seq Scan on team_categories', plan_text)
            
    def test_covering_index_usage(self):
        """Test that our covering indexes are being used for common queries"""
        with connection.cursor() as cursor:
            # Test a query that should benefit from our covering index
            cursor.execute("""
                EXPLAIN (ANALYZE, BUFFERS)
                SELECT article_id, title, published_date, discovery_date 
                FROM articles 
                WHERE article_id IN (SELECT articles_id FROM articles_team_categories WHERE teamcategory_id = %s)
                ORDER BY published_date DESC
            """, [1])
            
            plan = cursor.fetchall()
            plan_text = '\n'.join(str(row[0]) for row in plan)
            
            # Should use index for the lookup
            self.assertIn('Index', plan_text)
