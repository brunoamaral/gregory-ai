from django_filters import rest_framework as filters
from django.db import models
from gregory.models import Articles, Trials, Authors

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

class TrialFilter(filters.FilterSet):
    """
    Filter class for Trials, allowing searching by title, summary,
    and combined search across both fields, plus filtering by recruitment status.
    """
    title = filters.CharFilter(lookup_expr='icontains')
    summary = filters.CharFilter(field_name='summary', lookup_expr='icontains')
    search = filters.CharFilter(method='filter_search')
    status = filters.CharFilter(field_name='recruitment_status', lookup_expr='exact')
    
    class Meta:
        model = Trials
        fields = ['title', 'summary', 'search', 'status']
    
    def filter_search(self, queryset, name, value):
        """
        Search in both title and summary fields
        """
        return queryset.filter(
            models.Q(title__icontains=value) |
            models.Q(summary__icontains=value)
        )

class AuthorFilter(filters.FilterSet):
    """Filter class for Authors, allowing searching by full name."""

    full_name = filters.CharFilter(method='filter_full_name')

    class Meta:
        model = Authors
        fields = ['full_name']

    def filter_full_name(self, queryset, name, value):
        """Search in the full_name database field"""
        return queryset.filter(full_name__icontains=value)
