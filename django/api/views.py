from django.shortcuts import render
from gregory.models import Articles, Trials
from rest_framework import viewsets
from rest_framework import permissions
from rest_framework import generics

from api.serializers import ArticleSerializer, TrialSerializer

# Create your views here.
class ArticleViewSet(viewsets.ModelViewSet):
	"""
	List all articles in the database by published date
	"""
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


class RelevantList(generics.ListAPIView):
    serializer_class = ArticleSerializer

    def get_queryset(self):
        """
		Lists the articles that the admin has marked as relevant
        """
        return Articles.objects.filter(relevant="True")


# class PurchaseList(generics.ListAPIView):
#     serializer_class = PurchaseSerializer

#     def get_queryset(self):
#         """
#         This view should return a list of all the purchases for
#         the user as determined by the username portion of the URL.
#         """
#         username = self.kwargs['username']
#         return Purchase.objects.filter(purchaser__username=username)
