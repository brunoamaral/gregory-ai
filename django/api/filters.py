from django_filters import rest_framework as filters
from django.db import models
from gregory.models import Articles

class ArticleFilter(filters.FilterSet):
    """
    Filter class for Articles, allowing searching by title, abstract, 
    and combined search across both fields.
    """
    title = filters.CharFilter(lookup_expr='icontains')
    summary = filters.CharFilter(field_name='summary', lookup_expr='icontains')
    search = filters.CharFilter(method='filter_search')
    
    class Meta:
        model = Articles
        fields = ['title', 'summary', 'search']
    
    def filter_search(self, queryset, name, value):
        """
        Search in both title and summary fields
        """
        return queryset.filter(
            models.Q(title__icontains=value) | 
            models.Q(summary__icontains=value)
        )
