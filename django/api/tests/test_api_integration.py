from django.test import TestCase, Client
from django.urls import reverse
import json


class ArticleAPIFilterTest(TestCase):
	"""Test that the /articles/ API endpoint handles invalid last_days parameters correctly"""
	
	def setUp(self):
		self.client = Client()
	
	def test_articles_api_with_invalid_last_days(self):
		"""Test that the API doesn't crash with invalid last_days values"""
		# Test cases that previously caused the production error
		invalid_values = [
			'invalid_string',
			'',
			'abc123',
			'null',
			'undefined',
			'NaN',
		]
		
		for invalid_value in invalid_values:
			with self.subTest(last_days=invalid_value):
				try:
					# This should NOT return a 500 error anymore
					response = self.client.get('/api/articles/', {'last_days': invalid_value})
					
					# Should return either 200 (success) or 400 (bad request), but not 500 (server error)
					self.assertIn(response.status_code, [200, 400], 
						f"API returned {response.status_code} for last_days='{invalid_value}'. Expected 200 or 400.")
					
					print(f"✓ last_days='{invalid_value}': Status {response.status_code} (OK)")
					
				except Exception as e:
					self.fail(f"✗ API crashed with last_days='{invalid_value}': {e}")
	
	def test_articles_api_with_valid_last_days(self):
		"""Test that valid last_days values still work"""
		valid_values = ['7', '30', '1', '365']
		
		for valid_value in valid_values:
			with self.subTest(last_days=valid_value):
				try:
					response = self.client.get('/api/articles/', {'last_days': valid_value})
					self.assertEqual(response.status_code, 200, 
						f"API failed for valid last_days='{valid_value}'")
					
					# Should return JSON
					if response.content:
						json.loads(response.content)
					
					print(f"✓ last_days='{valid_value}': Status {response.status_code} (OK)")
					
				except json.JSONDecodeError:
					self.fail(f"✗ API returned invalid JSON for last_days='{valid_value}'")
				except Exception as e:
					self.fail(f"✗ API failed for last_days='{valid_value}': {e}")
