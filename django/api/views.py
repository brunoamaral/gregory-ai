from django.shortcuts import render
from gregory.models import Articles, Trials, Sources
from rest_framework import viewsets
from rest_framework import permissions
from rest_framework import generics

from api.serializers import ArticleSerializer, TrialSerializer, SourceSerializer

# Create your views here.
class ArticleViewSet(viewsets.ModelViewSet):
	"""
	List all articles in the database by published date
	"""
	queryset = Articles.objects.all().order_by('-published_date')
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



class TrialViewSet(viewsets.ModelViewSet):
	"""
	List all clinical trials by discovery date
	"""
	queryset = Trials.objects.all().order_by('-discovery_date')
	serializer_class = TrialSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]

class AllTrialViewSet(generics.ListAPIView):
	"""
	List all clinical trials by discovery date
	"""
	pagination_class = None
	queryset = Trials.objects.all().order_by('-discovery_date')
	serializer_class = TrialSerializer
	permission_classes = [permissions.IsAuthenticatedOrReadOnly]


class SourceViewSet(viewsets.ModelViewSet):
	"""
	List all sources of data
	"""
	queryset = Sources.objects.all().order_by('name')
	serializer_class = SourceSerializer
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
		return Articles.objects.all().exclude(sent_to_subscribers =True)

class ArticlesBySourceList(generics.ListAPIView):
	"""
	Lists the articles that come from the specified source_id
	"""
	serializer_class = ArticleSerializer

	def get_queryset(self):

		source = self.kwargs['source']
		return Articles.objects.filter(source=source)

class TrialsBySourceList(generics.ListAPIView):
	serializer_class = ArticleSerializer

	def get_queryset(self):
		"""
		Lists the clinical trials that come from the specified source_id
		"""
		source = self.kwargs['source']
		return Trials.objects.filter(source=source)

# class PurchaseList(generics.ListAPIView):
#     serializer_class = PurchaseSerializer

#     def get_queryset(self):
#         """
#         This view should return a list of all the purchases for
#         the user as determined by the username portion of the URL.
#         """
#         username = self.kwargs['username']
#         return Purchase.objects.filter(purchaser__username=username)
