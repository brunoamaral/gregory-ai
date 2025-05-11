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
