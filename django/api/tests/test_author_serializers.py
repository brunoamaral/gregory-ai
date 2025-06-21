from django.test import TestCase
from rest_framework.test import APITestCase
from gregory.models import Authors
from api.serializers import AuthorSerializer, ArticleAuthorSerializer

class AuthorSerializerTest(TestCase):
    def setUp(self):
        # Create a test author
        self.author = Authors.objects.create(
            given_name="John",
            family_name="Doe",
            ORCID="0000-0001-2345-6789"
        )
        
    def test_author_serializer_includes_full_name(self):
        # Test AuthorSerializer
        serializer = AuthorSerializer(self.author)
        data = serializer.data
        
        self.assertIn('full_name', data)
        self.assertEqual(data['full_name'], "John Doe")
        
    def test_article_author_serializer_includes_full_name(self):
        # Test ArticleAuthorSerializer
        serializer = ArticleAuthorSerializer(self.author)
        data = serializer.data
        
        self.assertIn('full_name', data)
        self.assertEqual(data['full_name'], "John Doe")
