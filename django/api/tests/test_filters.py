from django.test import TestCase, RequestFactory
from django.utils import timezone
from datetime import timedelta
from django.contrib.auth.models import User

from api.filters import ArticleFilter
from gregory.models import Articles


class ArticleFilterTestCase(TestCase):
	def setUp(self):
		"""Set up test data"""
		# Create test articles with specific discovery dates  
		self.article1 = Articles.objects.create(
			title="Test Article 1",
			link="https://example.com/1",
			discovery_date=timezone.now() - timedelta(days=5)
		)
		self.article2 = Articles.objects.create(
			title="Test Article 2", 
			link="https://example.com/2",
			discovery_date=timezone.now() - timedelta(days=15)
		)
		self.article3 = Articles.objects.create(
			title="Test Article 3",
			link="https://example.com/3", 
			discovery_date=timezone.now() - timedelta(days=25)
		)

	def test_filter_last_days_valid_integer(self):
		"""Test filter_last_days with valid integer"""
		queryset = Articles.objects.all()
		
		# Create a mock request
		factory = RequestFactory()
		request = factory.get('/articles/', {'last_days': 10})
		
		# Initialize filter with request
		filter_instance = ArticleFilter(request.GET, queryset=queryset, request=request)
		
		# Test with valid integer - should return articles from last 10 days
		result = filter_instance.filter_last_days(queryset, 'last_days', 10)
		self.assertEqual(result.count(), 1)  # Only article1 (5 days old) should match

	def test_filter_last_days_valid_string_integer(self):
		"""Test filter_last_days with string that can be converted to integer"""
		queryset = Articles.objects.all()
		
		# Create a mock request
		factory = RequestFactory()
		request = factory.get('/articles/', {'last_days': '20'})
		
		# Initialize filter with request
		filter_instance = ArticleFilter(request.GET, queryset=queryset, request=request)
		
		# Test with string integer - should work after conversion
		result = filter_instance.filter_last_days(queryset, 'last_days', '20')
		self.assertEqual(result.count(), 2)  # article1 (5 days) and article2 (15 days) should match

	def test_filter_last_days_invalid_string(self):
		"""Test filter_last_days with invalid string"""
		queryset = Articles.objects.all()
		
		# Create a mock request
		factory = RequestFactory()
		request = factory.get('/articles/', {'last_days': 'invalid'})
		
		# Initialize filter with request
		filter_instance = ArticleFilter(request.GET, queryset=queryset, request=request)
		
		# Test with invalid string - should return all articles (no filtering)
		result = filter_instance.filter_last_days(queryset, 'last_days', 'invalid')
		self.assertEqual(result.count(), 3)  # Should return all articles

	def test_filter_last_days_none_value(self):
		"""Test filter_last_days with None value"""
		queryset = Articles.objects.all()
		
		# Create a mock request
		factory = RequestFactory()
		request = factory.get('/articles/')
		
		# Initialize filter with request
		filter_instance = ArticleFilter(request.GET, queryset=queryset, request=request)
		
		# Test with None - should return all articles (no filtering)
		result = filter_instance.filter_last_days(queryset, 'last_days', None)
		self.assertEqual(result.count(), 3)  # Should return all articles

	def test_filter_last_days_empty_string(self):
		"""Test filter_last_days with empty string"""
		queryset = Articles.objects.all()
		
		# Create a mock request
		factory = RequestFactory()
		request = factory.get('/articles/', {'last_days': ''})
		
		# Initialize filter with request
		filter_instance = ArticleFilter(request.GET, queryset=queryset, request=request)
		
		# Test with empty string - should return all articles (no filtering)
		result = filter_instance.filter_last_days(queryset, 'last_days', '')
		self.assertEqual(result.count(), 3)  # Should return all articles

	def test_filter_last_days_negative_number(self):
		"""Test filter_last_days with negative number"""
		queryset = Articles.objects.all()
		
		# Create a mock request
		factory = RequestFactory()
		request = factory.get('/articles/', {'last_days': '-5'})
		
		# Initialize filter with request
		filter_instance = ArticleFilter(request.GET, queryset=queryset, request=request)
		
		# Test with negative number - should return all articles (no filtering)
		result = filter_instance.filter_last_days(queryset, 'last_days', -5)
		self.assertEqual(result.count(), 3)  # Should return all articles

	def test_filter_last_days_zero(self):
		"""Test filter_last_days with zero"""
		queryset = Articles.objects.all()
		
		# Create a mock request
		factory = RequestFactory()
		request = factory.get('/articles/', {'last_days': '0'})
		
		# Initialize filter with request
		filter_instance = ArticleFilter(request.GET, queryset=queryset, request=request)
		
		# Test with zero - should return all articles (no filtering)
		result = filter_instance.filter_last_days(queryset, 'last_days', 0)
		self.assertEqual(result.count(), 3)  # Should return all articles

	def test_filter_last_days_float_string(self):
		"""Test filter_last_days with float string"""
		queryset = Articles.objects.all()
		
		# Create a mock request
		factory = RequestFactory()
		request = factory.get('/articles/', {'last_days': '10.5'})
		
		# Initialize filter with request
		filter_instance = ArticleFilter(request.GET, queryset=queryset, request=request)
		
		# Test with float string - should convert and work
		result = filter_instance.filter_last_days(queryset, 'last_days', '10.5')
		self.assertEqual(result.count(), 1)  # Only article1 should match

	def test_filter_last_days_large_number(self):
		"""Test filter_last_days with large number"""
		queryset = Articles.objects.all()
		
		# Create a mock request
		factory = RequestFactory()
		request = factory.get('/articles/', {'last_days': '100'})
		
		# Initialize filter with request
		filter_instance = ArticleFilter(request.GET, queryset=queryset, request=request)
		
		# Test with large number - should return all articles
		result = filter_instance.filter_last_days(queryset, 'last_days', 100)
		self.assertEqual(result.count(), 3)  # Should return all articles
