from api.serializers import ArticleSerializer, TrialSerializer, SourceSerializer, CountArticlesSerializer, AuthorSerializer
from django.db.models.functions import Length
from django.db.models.query import QuerySet
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from gregory.models import Articles, Trials, Sources, Authors, Categories
from rest_framework import viewsets, permissions, generics, filters
import json

###
# ARTICLES
### 
class ArticleViewSet(viewsets.ModelViewSet):
	"""
	List all articles in the database by published date
	"""
	queryset = Articles.objects.all().order_by('-published_date')
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [filters.SearchFilter]
	search_fields  = ['$title','$summary']

class RelatedArticles(viewsets.ModelViewSet):
	"""
	Search related articles by the noun_phrases field. This search accepts regular expressions such as /articles/related/?search=<noun_phrase>|<noun_phrase>
	"""
	queryset = Articles.objects.all().order_by('-published_date')
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [filters.SearchFilter]
	search_fields  = ['$noun_phrases']


class ArticlesByCategory(viewsets.ModelViewSet):
	"""
	Search articles by the category field. Usage /articles/category/<category>/
	"""
	def get_queryset(self):
		category = self.kwargs.get('category', None)
		category = Categories.objects.filter(category_name__iregex=category)
		return Articles.objects.filter(categories=category.first()).order_by('-article_id')

	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]


class ArticlesBySubject(viewsets.ModelViewSet):
	"""
	Search articles by the subject field. Usage /articles/subject/<subject>/.
	Subject should be lower case and spaces should be replaced by dashes, for example: Multiple Sclerosis becomes multiple-sclerosis.
	"""
	def get_queryset(self):
		subject = self.kwargs.get('subject', None)
		subject = subject.replace('-', ' ')
		subject = Sources.objects.filter(subject__subject_name__iregex=subject)
		return Articles.objects.filter(source__in=subject).order_by('-article_id')

	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]


class AllArticleViewSet(generics.ListAPIView):
	"""
	List all articles 
	"""
	pagination_class = None
	queryset = Articles.objects.all().order_by('-published_date')
	serializer_class = ArticleSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

class RelevantList(generics.ListAPIView):
	serializer_class = ArticleSerializer

	def get_queryset(self):
		"""
		Lists the articles that the admin has marked as relevant
		"""
		return Articles.objects.filter(relevant="True")

class UnsentList(generics.ListAPIView):
	"""
	Lists the articles that have not been sent to subscribers
	"""
	serializer_class = ArticleSerializer

	def get_queryset(self):
		return Articles.objects.all().exclude(sent_to_subscribers = True)

class ArticlesBySourceList(generics.ListAPIView):
	"""
	Lists the articles that come from the specified source_id
	"""
	serializer_class = ArticleSerializer

	def get_queryset(self):

		source = self.kwargs['source']
		return Articles.objects.filter(source=source)

class ArticlesByAuthorList(generics.ListAPIView):
	"""
	Lists the articles that include the specified author_id
	"""
	serializer_class = ArticleSerializer

	def get_queryset(self):

		author = self.kwargs['author']
		return Articles.objects.filter(authors=author)

# class ArticleRelevant(generics.RetrieveUpdateAPIView):
# 	"""
# 	Change the value of relevancy of the article
# 	"""
# 	http_method_names = ['get','put']
# 	serializer_class = ArticleSerializer
# 	lookup_field = 'article_id'
# 	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
# 	def get_queryset(self):
# 		return Articles.objects.filter(article_id = self.kwargs['article_id'])

# 	def update(self,request,article_id=None,*args, **kwargs):
# 		instance = self.get_object()
# 		value = self.request.data.get("value", None)  # read data from request

# 		if value == 1:
# 			value = True
# 		elif value == 0:
# 			value = False
# 		data = {"relevant": value}
# 		serializer = ArticleSerializer(instance,data,partial=True)
# 		if serializer.is_valid():
# 			serializer.save()
# 			return HttpResponse(serializer.data)
# 		else: 
# 			return HttpResponse(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class ArticlesByKeyword(generics.ListAPIView):
	"""
	List articles by keyword
	"""
	serializer_class = ArticleSerializer
	permissions_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [filters.SearchFilter]
	search_fields = ['title','summary']

class ArticlesPredictionNone(generics.ListAPIView):
	"""
	List articles where the Machine Learning prediction is Null and summary length greater than 0 characters.    
	To override the default summary length pass the summary_length argument to the url as `/articles/prediction/none/?summary_length=42` 
	"""
	serializer_class = ArticleSerializer
	permissions_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_queryset(self):
		queryset = Articles.objects.annotate(summary_len=Length('summary')).filter( summary_len__gt = 0).filter(ml_prediction_gnb=None)
		summary_length = self.request.query_params.get('summary_length')
		if summary_length is not None:
			queryset = Articles.objects.annotate(summary_len=Length('summary')).filter( summary_len__gt = summary_length).filter(ml_prediction_gnb=None)
		return queryset

class ArticlesCount(viewsets.ModelViewSet):
	"""
	List all articles in the database by published date
	"""

	queryset = Articles.objects.raw('select count(*),article_id from articles group by article_id limit 1;')
	serializer_class = CountArticlesSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	pagination_classes = None

	# def get_queryset(self):
	# 	return HttpResponse(Articles.objects.count())
	# def list(self, request, *args, **kwargs):
	# 	queryset = self.filter_queryset(self.get_queryset())
	# 	# If you want response all the results, without pagination, 
	# 	# stop calling the self.paginate_queryset method, use queryset directly
	# 	page = self.paginate_queryset(queryset) or queryset
	# 	serializer = self.get_serializer(page, many=True)
	# 	return HttpResponse(json.dumps(serializer.data))

###
# TRIALS
### 

class TrialViewSet(viewsets.ModelViewSet):
	"""
	List all clinical trials by discovery date. Accepts regular expressions in search.
	"""
	queryset = Trials.objects.all().order_by('-discovery_date')
	serializer_class = TrialSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]
	filter_backends = [filters.SearchFilter]
	search_fields  = ['$title','$summary']

class AllTrialViewSet(generics.ListAPIView):
	"""
	List all clinical trials by discovery date
	"""
	pagination_class = None
	queryset = Trials.objects.all().order_by('-discovery_date')
	serializer_class = TrialSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]


class TrialsBySourceList(generics.ListAPIView):
	serializer_class = ArticleSerializer

	def get_queryset(self):
		"""
		Lists the clinical trials that come from the specified source_id
		"""
		source = self.kwargs['source']
		return Trials.objects.filter(source=source)

# class TrialsByKeyword(generics.ListAPIView):
# 	"""
# 	List clinical trials by keyword
# 	"""
# 	serializer_class = TrialSerializer
# 	permissions_classes = [permissions.IsAuthenticatedOrReadOnly]
# 	filter_backends = [filters.SearchFilter]
# 	search_fields = ['$title','$summary']



###
# SOURCES
### 

class SourceViewSet(viewsets.ModelViewSet):
	"""
	List all sources of data
	"""
	queryset = Sources.objects.all().order_by('name')
	serializer_class = SourceSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]


###
# AUTHORS
### 

class AuthorsViewSet(viewsets.ModelViewSet):
	"""
	List all authors
	"""
	queryset = Authors.objects.all().order_by('author_id')
	serializer_class = AuthorSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]


