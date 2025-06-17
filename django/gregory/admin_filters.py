from django.contrib import admin
from django.utils import timezone
import datetime

class DateRangeFilter(admin.SimpleListFilter):
    """
    Filter to look up PredictionRunLog entries within specific date ranges
    """
    title = 'Date Range'
    parameter_name = 'date_range'
    
    def lookups(self, request, model_admin):
        """
        Returns a list of tuples representing the filter options
        """
        return (
            ('today', 'Today'),
            ('past_24_hours', 'Past 24 hours'),
            ('past_7_days', 'Past 7 days'),
            ('past_30_days', 'Past 30 days'),
            ('this_month', 'This month'),
            ('this_year', 'This year'),
        )
    
    def queryset(self, request, queryset):
        """
        Filters the queryset based on the selected date range
        """
        now = timezone.now()
        
        if self.value() == 'today':
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return queryset.filter(run_started__gte=start_date)
        
        elif self.value() == 'past_24_hours':
            start_date = now - datetime.timedelta(hours=24)
            return queryset.filter(run_started__gte=start_date)
        
        elif self.value() == 'past_7_days':
            start_date = now - datetime.timedelta(days=7)
            return queryset.filter(run_started__gte=start_date)
        
        elif self.value() == 'past_30_days':
            start_date = now - datetime.timedelta(days=30)
            return queryset.filter(run_started__gte=start_date)
        
        elif self.value() == 'this_month':
            start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            return queryset.filter(run_started__gte=start_date)
        
        elif self.value() == 'this_year':
            start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            return queryset.filter(run_started__gte=start_date)
        
        return queryset

class SourceHealthFilter(admin.SimpleListFilter):
    """
    Filter sources based on their health status
    """
    title = 'Health Status'
    parameter_name = 'health_status'
    
    def lookups(self, request, model_admin):
        """
        Returns a list of tuples representing the filter options
        """
        return (
            ('healthy', 'Healthy (updated in last 30 days)'),
            ('warning', 'Warning (30-60 days since update)'),
            ('error', 'Error (60+ days since update)'),
            ('inactive', 'Inactive'),
            ('no_content', 'No Content')
        )
    
    def queryset(self, request, queryset):
        """
        Filters the queryset based on the selected health status.
        Uses the same logic for both article and trial sources.
        """
        if self.value() == 'healthy':
            thirty_days_ago = timezone.now() - datetime.timedelta(days=30)
            source_ids = []
            for source in queryset:
                if source.active:
                    if source.source_for == 'trials':
                        latest_date = source.get_latest_trial_date()
                    else:
                        latest_date = source.get_latest_article_date()
                        
                    if latest_date and latest_date >= thirty_days_ago:
                        source_ids.append(source.source_id)
            return queryset.filter(source_id__in=source_ids)
            
        elif self.value() == 'warning':
            thirty_days_ago = timezone.now() - datetime.timedelta(days=30)
            sixty_days_ago = timezone.now() - datetime.timedelta(days=60)
            source_ids = []
            for source in queryset:
                if source.active:
                    if source.source_for == 'trials':
                        latest_date = source.get_latest_trial_date()
                    else:
                        latest_date = source.get_latest_article_date()
                        
                    if latest_date and sixty_days_ago <= latest_date < thirty_days_ago:
                        source_ids.append(source.source_id)
            return queryset.filter(source_id__in=source_ids)
            
        elif self.value() == 'error':
            sixty_days_ago = timezone.now() - datetime.timedelta(days=60)
            source_ids = []
            for source in queryset:
                if source.active:
                    if source.source_for == 'trials':
                        latest_date = source.get_latest_trial_date()
                    else:
                        latest_date = source.get_latest_article_date()
                        
                    if latest_date and latest_date < sixty_days_ago:
                        source_ids.append(source.source_id)
            return queryset.filter(source_id__in=source_ids)
            
        elif self.value() == 'inactive':
            return queryset.filter(active=False)
            
        elif self.value() == 'no_content':
            source_ids = []
            for source in queryset:
                if source.active:
                    if source.source_for == 'trials':
                        has_content = source.trials_set.exists()
                    else:
                        has_content = source.articles_set.exists()
                        
                    if not has_content:
                        source_ids.append(source.source_id)
            return queryset.filter(source_id__in=source_ids)
            
        return queryset
