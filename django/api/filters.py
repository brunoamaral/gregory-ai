from django_filters import rest_framework as filters
from django.db import models
from gregory.models import Articles, Trials, Authors, Sources, TeamCategory, Subject

class ArticleFilter(filters.FilterSet):
    """
    Filter class for Articles, allowing searching by title, summary,
    and combined search across both fields, plus filtering by author,
    category, journal, team, and subject.
    """
    title = filters.CharFilter(method='filter_title')
    summary = filters.CharFilter(method='filter_summary')
    search = filters.CharFilter(method='filter_search')
    author_id = filters.NumberFilter(field_name='authors__author_id', lookup_expr='exact')
    category_slug = filters.CharFilter(field_name='team_categories__category_slug', lookup_expr='exact')
    category_id = filters.NumberFilter(field_name='team_categories__id', lookup_expr='exact')
    journal_slug = filters.CharFilter(method='filter_journal')
    team_id = filters.NumberFilter(field_name='teams__id', lookup_expr='exact')
    subject_id = filters.NumberFilter(field_name='subjects__id', lookup_expr='exact')
    source_id = filters.NumberFilter(field_name='sources__source_id', lookup_expr='exact')
    
    class Meta:
        model = Articles
        fields = ['title', 'summary', 'search', 'author_id', 'category_slug', 'category_id', 'journal_slug', 'team_id', 'subject_id', 'source_id']
    
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
    
    def filter_journal(self, queryset, name, value):
        """
        Filter by journal using case-insensitive regex matching.
        Handles URL-encoded journal names.
        """
        from urllib.parse import unquote
        journal_name = unquote(value)
        return queryset.filter(container_title__iregex=f'^{journal_name}$')

class TrialFilter(filters.FilterSet):
    """
    Filter class for Trials, allowing searching by title, summary,
    and combined search across both fields, plus filtering by recruitment status,
    team, and subject.
    """
    title = filters.CharFilter(method='filter_title')
    summary = filters.CharFilter(method='filter_summary')
    search = filters.CharFilter(method='filter_search')
    status = filters.CharFilter(field_name='recruitment_status', lookup_expr='exact')
    team_id = filters.NumberFilter(field_name='teams__id', lookup_expr='exact')
    subject_id = filters.NumberFilter(field_name='subjects__id', lookup_expr='exact')
    category_slug = filters.CharFilter(field_name='team_categories__category_slug', lookup_expr='exact')
    category_id = filters.NumberFilter(field_name='team_categories__id', lookup_expr='exact')
    source_id = filters.NumberFilter(field_name='sources__source_id', lookup_expr='exact')
    
    class Meta:
        model = Trials
        fields = ['title', 'summary', 'search', 'status', 'team_id', 'subject_id', 'category_slug', 'category_id', 'source_id']
    
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
    """Filter class for Authors, allowing searching by full name and filtering by author ID."""

    full_name = filters.CharFilter(method='filter_full_name')
    author_id = filters.NumberFilter(field_name='author_id', lookup_expr='exact')

    class Meta:
        model = Authors
        fields = ['full_name', 'author_id']

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

class SubjectFilter(filters.FilterSet):
    """
    Filter class for Subject, allowing filtering by team.
    """
    team_id = filters.NumberFilter(field_name='team__id', lookup_expr='exact')
    
    class Meta:
        model = Subject
        fields = ['team_id']

class CategoryFilter(filters.FilterSet):
    """
    Filter class for TeamCategory, allowing filtering by team and subject.
    """
    category_id = filters.NumberFilter(field_name='id', lookup_expr='exact')
    team_id = filters.NumberFilter(field_name='team__id', lookup_expr='exact')
    subject_id = filters.NumberFilter(field_name='subjects__id', lookup_expr='exact')
    
    class Meta:
        model = TeamCategory
        fields = ['category_id', 'team_id', 'subject_id']
