from api.serializers import ArticleSerializer, TrialSerializer, SourceSerializer, CountArticlesSerializer
from django.db.models.functions import Length
from django.db.models.query import QuerySet
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from gregory.models import Articles, Trials, Sources
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
	List articles where the Machine Learning prediction is Null and summary over 360 characters
	"""
	serializer_class = ArticleSerializer
	permissions_classes = [permissions.IsAuthenticatedOrReadOnly]

	def get_queryset(self):
		return Articles.objects.annotate(summary_len=Length('summary')).filter( summary_len__gt = 360)

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


