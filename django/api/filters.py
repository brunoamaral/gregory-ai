from django_filters import rest_framework as filters
from django.db import models
from django.utils import timezone
from django import forms
from datetime import datetime, timedelta
from gregory.models import Articles, Trials, Authors, Sources, TeamCategory, Subject

class ArticleFilter(filters.FilterSet):
    """
    Filter class for Articles, allowing searching by title, summary,
    and combined search across both fields, plus filtering by author,
    category, journal, team, subject, and special article types.
    """
    title = filters.CharFilter(method='filter_title', label='Title')
    summary = filters.CharFilter(method='filter_summary', label='Summary')
    search = filters.CharFilter(method='filter_search', label='Search')
    author_id = filters.NumberFilter(field_name='authors__author_id', lookup_expr='exact', label='Author ID')
    doi = filters.CharFilter(field_name='doi', lookup_expr='iexact', label='DOI')
    category_slug = filters.CharFilter(field_name='team_categories__category_slug', lookup_expr='exact', label='Category Slug')
    category_id = filters.NumberFilter(field_name='team_categories__id', lookup_expr='exact', label='Category ID')
    journal_slug = filters.CharFilter(method='filter_journal', label='Journal')
    team_id = filters.NumberFilter(field_name='teams__id', lookup_expr='exact', label='Team ID')
    subject_id = filters.NumberFilter(field_name='subjects__id', lookup_expr='exact', label='Subject ID')
    source_id = filters.NumberFilter(field_name='sources__source_id', lookup_expr='exact', label='Source ID')
    
    # New parameters for special article types
    relevant = filters.BooleanFilter(method='filter_relevant', label='Relevant')
    ml_threshold = filters.NumberFilter(method='filter_ml_threshold', label='ML Threshold (0.0-1.0)', 
                                       widget=forms.NumberInput(attrs={'step': '0.01', 'min': '0.0', 'max': '1.0'}))
    open_access = filters.BooleanFilter(method='filter_open_access', label='Open Access')
    last_days = filters.NumberFilter(method='filter_last_days', label='Last Days')
    week = filters.NumberFilter(method='filter_week', label='Week')
    year = filters.NumberFilter(method='filter_year', label='Year')
    
    class Meta:
        model = Articles
        fields = [
            'title', 'summary', 'search', 'author_id', 'doi', 'category_slug', 'category_id', 
            'journal_slug', 'team_id', 'subject_id', 'source_id', 'relevant', 'ml_threshold',
            'open_access', 'last_days', 'week', 'year'
        ]
    
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
    
    def _parse_ml_threshold(self, default=0.8):
        """
        Helper method to safely parse ml_threshold parameter from request.
        Returns default value if parameter is missing, empty, or invalid.
        """
        try:
            threshold_param = self.request.GET.get('ml_threshold', str(default))
            if threshold_param == '' or threshold_param is None:
                return default
            else:
                threshold = float(threshold_param)
                if not 0.0 <= threshold <= 1.0:
                    return default  # Use default if invalid range
                return threshold
        except (ValueError, TypeError):
            return default  # Use default if conversion fails

    def _get_ml_relevant_articles_query(self, threshold=0.8):
        """
        Build a database-level query to find ML-relevant articles based on consensus logic.
        This avoids the N+1 query problem by using efficient Django ORM queries.
        """
        from django.db.models import Count, Q, Case, When, IntegerField
        
        # Get all article IDs that meet ML consensus for at least one subject
        ml_relevant_ids = set()
        
        # Get all subjects with auto_predict enabled
        auto_predict_subjects = Subject.objects.filter(auto_predict=True).values('id', 'ml_consensus_type')
        
        for subject_data in auto_predict_subjects:
            subject_id = subject_data['id']
            consensus_type = subject_data['ml_consensus_type']
            
            # Determine minimum algorithm count based on consensus type
            min_algorithms = {
                'any': 1,
                'majority': 2,
                'all': 3
            }.get(consensus_type, 1)  # Default to 'any' if unknown
            
            # Find articles that meet consensus for this subject
            articles_for_subject = Articles.objects.filter(
                subjects__id=subject_id,
                ml_predictions_detail__subject_id=subject_id,
                ml_predictions_detail__predicted_relevant=True,
                ml_predictions_detail__probability_score__gte=threshold
            ).annotate(
                algorithm_count=Count('ml_predictions_detail__algorithm', distinct=True)
            ).filter(
                algorithm_count__gte=min_algorithms
            ).values_list('article_id', flat=True)
            
            ml_relevant_ids.update(articles_for_subject)
        
        return Q(article_id__in=list(ml_relevant_ids))

    def filter_relevant(self, queryset, name, value):
        """
        Filter for relevant articles (ML predictions with consensus or manual selection)
        Uses ml_threshold parameter if provided, otherwise defaults to 0.8
        """
        if value:
            # Get ML threshold from request parameters, default to 0.8
            threshold = self._parse_ml_threshold(0.8)
            
            # Get articles that are either:
            # 1. Manually marked as relevant
            manually_relevant = models.Q(article_subject_relevances__is_relevant=True)
            
            # 2. ML-relevant based on subject-specific consensus settings and threshold
            ml_relevant_q = self._get_ml_relevant_articles_query(threshold)
            
            return queryset.filter(manually_relevant | ml_relevant_q).distinct()
        else:
            # Exclude articles that are either manually relevant or ML-relevant
            threshold = self._parse_ml_threshold(0.8)
            manually_relevant = models.Q(article_subject_relevances__is_relevant=True)
            ml_relevant_q = self._get_ml_relevant_articles_query(threshold)
            
            return queryset.exclude(manually_relevant | ml_relevant_q).distinct()
    
    def filter_ml_threshold(self, queryset, name, value):
        """
        Filter for articles with ML predictions above the specified threshold.
        Works independently or in combination with the relevant filter.
        """
        try:
            threshold = float(value)
            if not 0.0 <= threshold <= 1.0:
                # Invalid threshold, return empty queryset
                return queryset.none()
                
            # Use efficient database-level query instead of Python loop
            ml_relevant_q = self._get_ml_relevant_articles_query(threshold)
            return queryset.filter(ml_relevant_q)
            
        except (ValueError, TypeError):
            # Invalid threshold format, return empty queryset
            return queryset.none()
    
    def filter_open_access(self, queryset, name, value):
        """
        Filter for open access articles
        """
        if value:
            return queryset.filter(access='open')
        else:
            return queryset.exclude(access='open')
    
    def filter_last_days(self, queryset, name, value):
        """
        Filter for articles from the last X days
        """
        if not value:
            return queryset
            
        try:
            # Convert to float first, then int (handles '10.5' -> 10)
            days = int(float(value))
            if days <= 0:
                return queryset
            
            days_ago = timezone.now() - timedelta(days=days)
            return queryset.filter(discovery_date__gte=days_ago)
        except (ValueError, TypeError, OverflowError):
            # Return unfiltered queryset if value is invalid
            return queryset
    
    def filter_week(self, queryset, name, value):
        """
        Filter for articles from a specific week (requires year parameter)
        """
        year = self.request.GET.get('year')
        if value and year:
            try:
                week_num = int(value)
                year_num = int(year)
                
                # Calculate first and last day of the week
                first_day_of_week = datetime.strptime(f'{year_num}-W{week_num - 1}-1', "%Y-W%W-%w")
                last_day_of_week = first_day_of_week + timedelta(days=6.9)
                
                return queryset.filter(
                    discovery_date__gte=first_day_of_week.replace(tzinfo=timezone.get_current_timezone()),
                    discovery_date__lte=last_day_of_week.replace(tzinfo=timezone.get_current_timezone())
                )
            except (ValueError, TypeError):
                pass
        return queryset
    
    def filter_year(self, queryset, name, value):
        """
        Filter for articles from a specific year (used with week parameter)
        This filter doesn't modify the queryset directly - it's used by filter_week
        """
        return queryset

class TrialFilter(filters.FilterSet):
    """
    Filter class for Trials, allowing searching by title, summary,
    and combined search across both fields, plus filtering by recruitment status,
    team, and subject.
    """
    # Core search filters
    title = filters.CharFilter(method='filter_title', label='Title')
    summary = filters.CharFilter(method='filter_summary', label='Summary')
    search = filters.CharFilter(method='filter_search', label='Search')
    
    # ID and relationship filters
    trial_id = filters.NumberFilter(field_name='trial_id', lookup_expr='exact', label='Trial ID')
    team_id = filters.NumberFilter(field_name='teams__id', lookup_expr='exact', label='Team ID')
    subject_id = filters.NumberFilter(field_name='subjects__id', lookup_expr='exact', label='Subject ID')
    category_slug = filters.CharFilter(field_name='team_categories__category_slug', lookup_expr='exact', label='Category Slug')
    category_id = filters.NumberFilter(field_name='team_categories__id', lookup_expr='exact', label='Category ID')
    source_id = filters.NumberFilter(field_name='sources__source_id', lookup_expr='exact', label='Source ID')
    
    # Trial-specific filters
    recruitment_status = filters.CharFilter(field_name='recruitment_status', lookup_expr='exact')
    status = filters.CharFilter(field_name='recruitment_status', lookup_expr='exact')  # Legacy alias for backward compatibility
    internal_number = filters.CharFilter(field_name='internal_number', lookup_expr='icontains')
    phase = filters.CharFilter(field_name='phase', lookup_expr='icontains')
    study_type = filters.CharFilter(field_name='study_type', lookup_expr='icontains')
    primary_sponsor = filters.CharFilter(field_name='primary_sponsor', lookup_expr='icontains')
    source_register = filters.CharFilter(field_name='source_register', lookup_expr='icontains')
    countries = filters.CharFilter(field_name='countries', lookup_expr='icontains')
    
    # Medical/research filters
    condition = filters.CharFilter(field_name='condition', lookup_expr='icontains')
    intervention = filters.CharFilter(field_name='intervention', lookup_expr='icontains')
    therapeutic_areas = filters.CharFilter(field_name='therapeutic_areas', lookup_expr='icontains')
    inclusion_agemin = filters.CharFilter(field_name='inclusion_agemin', lookup_expr='exact')
    inclusion_agemax = filters.CharFilter(field_name='inclusion_agemax', lookup_expr='exact')
    inclusion_gender = filters.CharFilter(field_name='inclusion_gender', lookup_expr='icontains')
    
    class Meta:
        model = Trials
        fields = [
            'trial_id', 'title', 'summary', 'search', 'recruitment_status', 'status',
            'team_id', 'subject_id', 'category_slug', 'category_id', 'source_id',
            'internal_number', 'phase', 'study_type', 'primary_sponsor', 'source_register',
            'countries', 'condition', 'intervention', 'therapeutic_areas',
            'inclusion_agemin', 'inclusion_agemax', 'inclusion_gender'
        ]
    
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

    full_name = filters.CharFilter(method='filter_full_name', label='Full Name')
    author_id = filters.NumberFilter(field_name='author_id', lookup_expr='exact', label='Author ID')
    orcid = filters.CharFilter(field_name='ORCID', lookup_expr='icontains', label='ORCID')
    country = filters.CharFilter(field_name='country', lookup_expr='exact', label='Country')

    class Meta:
        model = Authors
        fields = ['full_name', 'author_id', 'orcid', 'country']

    def filter_full_name(self, queryset, name, value):
        """Search in the full_name database field using optimized uppercase column"""
        # Use uppercase search for better performance with GIN index
        upper_value = value.upper()
        return queryset.filter(ufull_name__contains=upper_value)

class SourceFilter(filters.FilterSet):
    """
    Filter class for Sources, allowing filtering by team and subject.
    """
    source_id = filters.NumberFilter(field_name='source_id', lookup_expr='exact', label='Source ID')
    team_id = filters.NumberFilter(field_name='team__id', lookup_expr='exact', label='Team ID')
    subject_id = filters.NumberFilter(field_name='subject__id', lookup_expr='exact', label='Subject ID')
    active = filters.BooleanFilter(field_name='active', label='Active')
    source_for = filters.CharFilter(field_name='source_for', lookup_expr='exact', label='Source For')
    link = filters.CharFilter(field_name='link', lookup_expr='icontains', label='Link')
    
    class Meta:
        model = Sources
        fields = ['source_id', 'team_id', 'subject_id', 'active', 'source_for', 'link']

class SubjectFilter(filters.FilterSet):
    """
    Filter class for Subject, allowing filtering by team.
    """
    team_id = filters.NumberFilter(field_name='team__id', lookup_expr='exact', label='Team ID')
    
    class Meta:
        model = Subject
        fields = ['team_id']

class CategoryFilter(filters.FilterSet):
    """
    Filter class for TeamCategory, allowing filtering by team and subject.
    """
    category_id = filters.NumberFilter(field_name='id', lookup_expr='exact', label='Category ID')
    team_id = filters.NumberFilter(field_name='team__id', lookup_expr='exact', label='Team ID')
    subject_id = filters.NumberFilter(field_name='subjects__id', lookup_expr='exact', label='Subject ID')
    category_terms = filters.CharFilter(method='filter_category_terms', label='Category Terms')
    
    class Meta:
        model = TeamCategory
        fields = ['category_id', 'team_id', 'subject_id', 'category_terms']
    
    def filter_category_terms(self, queryset, name, value):
        """Filter by category terms using array overlap"""
        return queryset.filter(category_terms__icontains=value)
