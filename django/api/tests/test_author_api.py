from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from gregory.models import Authors

class AuthorAPITest(TestCase):
    def setUp(self):
        # Create a test author
        self.author = Authors.objects.create(
            given_name="Jane",
            family_name="Smith",
            ORCID="0000-0002-3456-7890"
        )
        self.client = APIClient()
        
    def test_author_api_includes_full_name(self):
        # Test the author detail endpoint
        url = f'/authors/{self.author.author_id}/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, 200)
        self.assertIn('full_name', response.data)
        self.assertEqual(response.data['full_name'], "Jane Smith")
