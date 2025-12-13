from django.test import TestCase, RequestFactory
from django.utils import timezone
from datetime import timedelta, datetime
from unittest.mock import patch

from api.filters import ArticleFilter
from gregory.models import Articles


class TimezonePatchDebugTestCase(TestCase):
	def setUp(self):
		"""Set up test data with fixed dates"""
		self.base_date = timezone.make_aware(datetime(2025, 1, 15, 12, 0, 0))
		
		# Create test articles with specific discovery dates
		self.article1 = Articles.objects.create(
			title="Test Article 1",
			link="https://example.com/1",
			discovery_date=self.base_date - timedelta(days=5)  # Jan 10
		)
		self.article2 = Articles.objects.create(
			title="Test Article 2", 
			link="https://example.com/2",
			discovery_date=self.base_date - timedelta(days=15)  # Dec 31
		)
		self.article3 = Articles.objects.create(
			title="Test Article 3",
			link="https://example.com/3", 
			discovery_date=self.base_date - timedelta(days=25)  # Dec 21
		)

	def test_timezone_now_without_patch(self):
		"""Test what timezone.now() returns without patching"""
		queryset = Articles.objects.all()
		factory = RequestFactory()
		request = factory.get('/articles/', {'last_days': 10})
		filter_instance = ArticleFilter(request.GET, queryset=queryset, request=request)
		
		# Call WITHOUT patching
		result = filter_instance.filter_last_days(queryset, 'last_days', 10)
		print(f"\nWithout patch: timezone.now() = {timezone.now()}")
		print(f"Without patch: Articles returned = {result.count()}")
		print(f"Without patch: Article discovery dates: {[self.article1.discovery_date, self.article2.discovery_date, self.article3.discovery_date]}")

	def test_timezone_now_with_django_utils_patch(self):
		"""Test patching django.utils.timezone.now"""
		queryset = Articles.objects.all()
		factory = RequestFactory()
		request = factory.get('/articles/', {'last_days': 10})
		filter_instance = ArticleFilter(request.GET, queryset=queryset, request=request)
		
		# Patch django.utils.timezone.now
		with patch('django.utils.timezone.now', return_value=self.base_date):
			print(f"\nWith django.utils patch: timezone.now() = {timezone.now()}")
			result = filter_instance.filter_last_days(queryset, 'last_days', 10)
			print(f"With django.utils patch: Articles returned = {result.count()}")

	def test_timezone_now_with_api_filters_patch(self):
		"""Test patching api.filters.timezone.now"""
		queryset = Articles.objects.all()
		factory = RequestFactory()
		request = factory.get('/articles/', {'last_days': 10})
		filter_instance = ArticleFilter(request.GET, queryset=queryset, request=request)
		
		# Patch api.filters.timezone.now (where timezone is imported)
		with patch('api.filters.timezone.now', return_value=self.base_date):
			print(f"\nWith api.filters patch: timezone.now() = {timezone.now()}")
			result = filter_instance.filter_last_days(queryset, 'last_days', 10)
			print(f"With api.filters patch: Articles returned = {result.count()}")
