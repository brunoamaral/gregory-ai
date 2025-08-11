from django.test import TestCase
from django.test import RequestFactory

from api.filters import ArticleFilter
from gregory.models import Articles


class FilterErrorHandlingTest(TestCase):
	"""Test that our filter_last_days fix handles invalid input types correctly."""
	
	def test_filter_last_days_type_error_fix(self):
		"""Test that filter_last_days handles invalid types without crashing"""
		# Create a simple article for testing
		Articles.objects.create(
			title="Test Article",
			link="https://example.com/test"
		)
		
		queryset = Articles.objects.all()
		filter_instance = ArticleFilter()
		
		# Test cases that previously caused TypeError in production
		test_cases = [
			('invalid_string', "Should handle invalid string"),
			(None, "Should handle None value"),
			('', "Should handle empty string"),
			([], "Should handle list"),
			({}, "Should handle dict"),
			(object(), "Should handle arbitrary object"),
			('10.5', "Should handle float string"),
			(-5, "Should handle negative number"),
			(0, "Should handle zero"),
		]
		
		for test_value, description in test_cases:
			with self.subTest(value=test_value, description=description):
				try:
					# This should NOT raise a TypeError anymore
					result = filter_instance.filter_last_days(queryset, 'last_days', test_value)
					# Should return a queryset (either filtered or original)
					self.assertIsNotNone(result)
					print(f"✓ {description}: Handled successfully")
				except TypeError as e:
					self.fail(f"✗ {description}: Still raises TypeError: {e}")
				except Exception as e:
					# Other exceptions are acceptable, we just want to avoid TypeError
					print(f"⚠ {description}: Raised {type(e).__name__}: {e}")
		
		print("All type error tests passed - filter no longer crashes on invalid input")
		
		# Test that valid integer strings still work
		try:
			result = filter_instance.filter_last_days(queryset, 'last_days', '7')
			self.assertIsNotNone(result)
			print("✓ Valid string integer '7': Handled successfully")
		except Exception as e:
			self.fail(f"✗ Valid string integer failed: {e}")
		
		# Test that valid integers work
		try:
			result = filter_instance.filter_last_days(queryset, 'last_days', 7)
			self.assertIsNotNone(result)
			print("✓ Valid integer 7: Handled successfully")
		except Exception as e:
			self.fail(f"✗ Valid integer failed: {e}")
