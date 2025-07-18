from django.test import TestCase
from django.db.models import Count
from gregory.models import Authors, Articles, Team, Subject, TeamCategory, Sources
from api.serializers import AuthorSerializer, ArticleAuthorSerializer
from organizations.models import Organization
from django_countries.fields import Country


class AuthorSerializerTest(TestCase):
    """Test cases for Author serializers"""
    
    def setUp(self):
        """Set up test data"""
        # Create test organization and team
        self.organization = Organization.objects.create(name="Test Org")
        self.team = Team.objects.create(
            organization=self.organization,
            name="Test Team",
            slug="test-team"
        )
        
        # Create test subject
        self.subject = Subject.objects.create(
            subject_name="Test Subject",
            subject_slug="test-subject",
            team=self.team
        )
        
        # Create test source
        self.source = Sources.objects.create(
            name="Test Source",
            team=self.team,
            subject=self.subject
        )
        
        # Create test authors
        self.author = Authors.objects.create(
            given_name="John",
            family_name="Doe",
            ORCID="0000-0001-2345-6789",
            country=Country('US')
        )
        
        self.author_no_country = Authors.objects.create(
            given_name="Jane",
            family_name="Smith",
            ORCID="0000-0002-3456-7890"
        )
        
        # Create test articles for article count testing
        self.article1 = Articles.objects.create(
            title="Test Article 1",
            link="http://example.com/article1",
            summary="Test summary 1"
        )
        self.article1.authors.add(self.author)
        self.article1.teams.add(self.team)
        self.article1.subjects.add(self.subject)
        self.article1.sources.add(self.source)
        
        self.article2 = Articles.objects.create(
            title="Test Article 2", 
            link="http://example.com/article2",
            summary="Test summary 2"
        )
        self.article2.authors.add(self.author)
        self.article2.teams.add(self.team)
        self.article2.subjects.add(self.subject)
        self.article2.sources.add(self.source)
        
    def test_author_serializer_basic_fields(self):
        """Test AuthorSerializer includes all required fields"""
        serializer = AuthorSerializer(self.author)
        data = serializer.data
        
        expected_fields = [
            'author_id', 'given_name', 'family_name', 'full_name', 
            'ORCID', 'country', 'articles_count', 'articles_list'
        ]
        
        for field in expected_fields:
            self.assertIn(field, data, f"Field '{field}' missing from serializer")
        
    def test_author_serializer_full_name(self):
        """Test AuthorSerializer correctly generates full_name"""
        serializer = AuthorSerializer(self.author)
        data = serializer.data
        
        self.assertEqual(data['full_name'], "John Doe")
        
    def test_author_serializer_country_code(self):
        """Test AuthorSerializer correctly handles country codes"""
        serializer = AuthorSerializer(self.author)
        data = serializer.data
        
        self.assertEqual(data['country'], 'US')
        
        # Test author without country
        serializer_no_country = AuthorSerializer(self.author_no_country)
        data_no_country = serializer_no_country.data
        
        self.assertIsNone(data_no_country['country'])
        
    def test_author_serializer_articles_count_fallback(self):
        """Test AuthorSerializer calculates articles_count when not annotated"""
        serializer = AuthorSerializer(self.author)
        data = serializer.data
        
        self.assertEqual(data['articles_count'], 2)
        
    def test_author_serializer_articles_count_annotated(self):
        """Test AuthorSerializer uses annotated article_count when available"""
        # Create queryset with annotation
        annotated_queryset = Authors.objects.annotate(
            article_count=Count('articles', distinct=True)
        )
        annotated_author = annotated_queryset.get(author_id=self.author.author_id)
        
        serializer = AuthorSerializer(annotated_author)
        data = serializer.data
        
        self.assertEqual(data['articles_count'], 2)
        
    def test_author_serializer_articles_list_url(self):
        """Test AuthorSerializer generates correct articles_list URL"""
        serializer = AuthorSerializer(self.author)
        data = serializer.data
        
        expected_url = f"https://api.example.com/articles/?author_id={self.author.author_id}"
        self.assertEqual(data['articles_list'], expected_url)
        
    def test_article_author_serializer_basic_fields(self):
        """Test ArticleAuthorSerializer includes all required fields"""
        serializer = ArticleAuthorSerializer(self.author)
        data = serializer.data
        
        expected_fields = ['author_id', 'given_name', 'family_name', 'full_name', 'ORCID', 'country']
        
        for field in expected_fields:
            self.assertIn(field, data, f"Field '{field}' missing from ArticleAuthorSerializer")
        
    def test_article_author_serializer_full_name(self):
        """Test ArticleAuthorSerializer correctly generates full_name"""
        serializer = ArticleAuthorSerializer(self.author)
        data = serializer.data
        
        self.assertEqual(data['full_name'], "John Doe")
        
    def test_article_author_serializer_excludes_articles_fields(self):
        """Test ArticleAuthorSerializer excludes article-related fields"""
        serializer = ArticleAuthorSerializer(self.author)
        data = serializer.data
        
        # These fields should not be in ArticleAuthorSerializer
        excluded_fields = ['articles_count', 'articles_list']
        
        for field in excluded_fields:
            self.assertNotIn(field, data, f"Field '{field}' should not be in ArticleAuthorSerializer")
