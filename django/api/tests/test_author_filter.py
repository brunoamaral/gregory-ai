from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from gregory.models import Authors

class AuthorFilterTests(TestCase):
    """Test cases for filtering authors by author_id and full_name parameters"""
    
    def setUp(self):
        # Create test authors
        self.author1 = Authors.objects.create(
            family_name="Smith",
            given_name="John",
            full_name="John Smith"
        )
        self.author2 = Authors.objects.create(
            family_name="Doe",
            given_name="Jane",
            full_name="Jane Doe"
        )
        self.author3 = Authors.objects.create(
            family_name="Johnson",
            given_name="Bob",
            full_name="Bob Johnson"
        )
        
        self.client = APIClient()
    
    def test_filter_authors_by_author_id(self):
        """Test filtering authors by author_id parameter"""
        response = self.client.get(f'/authors/?author_id={self.author1.author_id}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        results = response.data['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['author_id'], self.author1.author_id)
        self.assertEqual(results[0]['full_name'], "John Smith")
    
    def test_filter_authors_by_full_name(self):
        """Test filtering authors by full_name parameter"""
        response = self.client.get('/authors/?full_name=Jane%20Doe')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        results = response.data['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['author_id'], self.author2.author_id)
        self.assertEqual(results[0]['full_name'], "Jane Doe")
    
    def test_filter_authors_by_partial_name(self):
        """Test filtering authors by partial name"""
        response = self.client.get('/authors/?full_name=John')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        results = response.data['results']
        # Should find both "John Smith" and "Bob Johnson"
        self.assertEqual(len(results), 2)
        
        full_names = [author['full_name'] for author in results]
        self.assertIn("John Smith", full_names)
        self.assertIn("Bob Johnson", full_names)
    
    def test_filter_authors_by_nonexistent_author_id(self):
        """Test filtering authors by non-existent author_id"""
        response = self.client.get('/authors/?author_id=99999')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        results = response.data['results']
        self.assertEqual(len(results), 0)
    
    def test_filter_authors_by_nonexistent_name(self):
        """Test filtering authors by non-existent full_name"""
        response = self.client.get('/authors/?full_name=Nonexistent%20Author')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        results = response.data['results']
        self.assertEqual(len(results), 0)
    
    def test_authors_list_without_filters(self):
        """Test authors list endpoint without any filters"""
        response = self.client.get('/authors/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        results = response.data['results']
        self.assertEqual(len(results), 3)  # All three authors
    
    def test_case_insensitive_name_search(self):
        """Test that full_name search is case-insensitive"""
        response = self.client.get('/authors/?full_name=jane%20doe')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        results = response.data['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['full_name'], "Jane Doe")
        
        # Test uppercase
        response = self.client.get('/authors/?full_name=JANE%20DOE')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        results = response.data['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['full_name'], "Jane Doe")
