from django_filters import rest_framework as filters
from django.db import models
from gregory.models import Articles, Trials, Authors, Sources, TeamCategory

class ArticleFilter(filters.FilterSet):
    """
    Filter class for Articles, allowing searching by title, abstract, 
    and combined search across both fields.
    """
    title = filters.CharFilter(method='filter_title')
    summary = filters.CharFilter(method='filter_summary')
    search = filters.CharFilter(method='filter_search')
    
    class Meta:
        model = Articles
        fields = ['title', 'summary', 'search']
    
    def filter_title(self, queryset, name, value):
        """
        Search in title field using uppercase column for performance
        """
        return queryset.filter(utitle__contains=value.upper())
    
    def filter_summary(self, queryset, name, value):
        """
        Search in summary field using uppercase column for performance
        """
        return queryset.filter(usummary__contains=value.upper())
    
    def filter_search(self, queryset, name, value):
        """
        Search in both title and summary fields using uppercase columns for performance
        """
        upper_value = value.upper()
        return queryset.filter(
            models.Q(utitle__contains=upper_value) | 
            models.Q(usummary__contains=upper_value)
        )

class TrialFilter(filters.FilterSet):
    """
    Filter class for Trials, allowing searching by title, summary,
    and combined search across both fields, plus filtering by recruitment status.
    """
    title = filters.CharFilter(method='filter_title')
    summary = filters.CharFilter(method='filter_summary')
    search = filters.CharFilter(method='filter_search')
    status = filters.CharFilter(field_name='recruitment_status', lookup_expr='exact')
    
    class Meta:
        model = Trials
        fields = ['title', 'summary', 'search', 'status']
    
    def filter_title(self, queryset, name, value):
        """
        Search in title field using uppercase column for performance
        """
        return queryset.filter(utitle__contains=value.upper())
    
    def filter_summary(self, queryset, name, value):
        """
        Search in summary field using uppercase column for performance
        """
        return queryset.filter(usummary__contains=value.upper())
    
    def filter_search(self, queryset, name, value):
        """
        Search in both title and summary fields using uppercase columns for performance
        """
        upper_value = value.upper()
        return queryset.filter(
            models.Q(utitle__contains=upper_value) |
            models.Q(usummary__contains=upper_value)
        )

class AuthorFilter(filters.FilterSet):
    """Filter class for Authors, allowing searching by full name."""

    full_name = filters.CharFilter(method='filter_full_name')

    class Meta:
        model = Authors
        fields = ['full_name']

    def filter_full_name(self, queryset, name, value):
        """Search in the full_name database field using optimized uppercase column"""
        # Use uppercase search for better performance with GIN index
        upper_value = value.upper()
        return queryset.filter(ufull_name__contains=upper_value)

class SourceFilter(filters.FilterSet):
    """
    Filter class for Sources, allowing filtering by team and subject.
    """
    team_id = filters.NumberFilter(field_name='team__id', lookup_expr='exact')
    subject_id = filters.NumberFilter(field_name='subject__id', lookup_expr='exact')
    
    class Meta:
        model = Sources
        fields = ['team_id', 'subject_id']

class CategoryFilter(filters.FilterSet):
    """
    Filter class for TeamCategory, allowing filtering by team and subject.
    """
    team_id = filters.NumberFilter(field_name='team__id', lookup_expr='exact')
    subject_id = filters.NumberFilter(field_name='subjects__id', lookup_expr='exact')
    
    class Meta:
        model = TeamCategory
        fields = ['team_id', 'subject_id']
