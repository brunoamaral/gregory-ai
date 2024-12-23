# utils/subscription.py

from datetime import timedelta
from django.utils.timezone import now
from gregory.models import Articles, Trials

def get_trials_for_list(lst):
    """Returns trials discovered in the last 30 days for the given list."""
    return Trials.objects.filter(
        subjects__in=lst.subjects.all(),
        discovery_date__gte=now() - timedelta(days=30)
    ).distinct()

def get_articles_for_list(lst):
    """Returns articles discovered in the last 30 days for the given list."""
    return Articles.objects.filter(
        subjects__in=lst.subjects.all(),
        discovery_date__gte=now() - timedelta(days=30)
    ).distinct()