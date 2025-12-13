from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status
from gregory.models import TeamCategory, Team, Subject, Articles, Authors
from unittest.mock import Mock
import json


class CategoryAuthorsTestCase(TestCase):
    """Test cases for the enhanced categories endpoint with author statistics"""
    
    def setUp(self):
        self.client = APIClient()
        
        # Create test data (mocked since we can't run migrations)
        self.team = Mock()
        self.team.id = 1
        self.team.name = "Test Team"
        
        self.subject = Mock()
        self.subject.id = 1
        
        self.category = Mock()
        self.category.id = 1
        self.category.category_name = "Test Category"
        self.category.category_slug = "test-category"
        
        self.author = Mock()
        self.author.author_id = 1
        self.author.given_name = "John"
        self.author.family_name = "Doe"
        self.author.full_name = "John Doe"
        self.author.ORCID = "0000-0000-0000-0000"
        self.author.country = Mock()
        self.author.country.code = "US"
    
    def test_categories_endpoint_exists(self):
        """Test that the categories endpoint exists and accepts our new parameters"""
        # Since we can't actually run the database, we'll test the URL patterns
        url = reverse('categories-list')
        self.assertTrue(url.endswith('/categories/'))
    
    def test_category_serializer_fields(self):
        """Test that the CategorySerializer includes the new fields"""
        from api.serializers import CategorySerializer
        
        # Check that the new fields are in the Meta.fields
        # Note: monthly_counts is an optional field that can be included based on context parameters
        expected_fields = [
            'id', 'category_description', 'category_name', 'category_slug', 'category_terms', 
            'article_count_total', 'trials_count_total', 'authors_count', 'top_authors', 'monthly_counts'
        ]
        
        self.assertEqual(CategorySerializer.Meta.fields, expected_fields)
    
    def test_category_top_author_serializer_fields(self):
        """Test that CategoryTopAuthorSerializer has the correct fields"""
        from api.serializers import CategoryTopAuthorSerializer
        
        expected_fields = ['author_id', 'given_name', 'family_name', 'full_name', 'ORCID', 'country', 'articles_count']
        self.assertEqual(CategoryTopAuthorSerializer.Meta.fields, expected_fields)
    
    def test_category_viewset_has_authors_action(self):
        """Test that CategoryViewSet has the new authors action"""
        from api.views import CategoryViewSet
        
        # Check that the authors method exists
        self.assertTrue(hasattr(CategoryViewSet, 'authors'))
        
        # Check that it's decorated as an action
        method = getattr(CategoryViewSet, 'authors')
        self.assertTrue(hasattr(method, 'mapping'))
    
    def test_build_date_filters_method(self):
        """Test the _build_date_filters helper method"""
        from api.views import CategoryViewSet
        
        viewset = CategoryViewSet()
        # Mock the request
        viewset.request = Mock()
        
        # Test timeframe=year
        filters = viewset._build_date_filters(None, None, 'year')
        self.assertIn('articles__published_date__gte', filters)
        
        # Test date string parsing
        filters = viewset._build_date_filters('2024-01-01', '2024-12-31', None)
        self.assertIn('articles__published_date__gte', filters)
        self.assertIn('articles__published_date__lte', filters)
    
    def test_new_query_parameters_documented(self):
        """Test that the new query parameters are documented in the docstring"""
        from api.views import CategoryViewSet
        
        docstring = CategoryViewSet.__doc__
        self.assertIn('include_authors', docstring)
        self.assertIn('max_authors', docstring)
        self.assertIn('date_from', docstring)
        self.assertIn('date_to', docstring)
        self.assertIn('timeframe', docstring)