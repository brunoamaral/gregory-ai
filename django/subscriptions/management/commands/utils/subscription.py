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

def get_latest_research_by_category(lst, days=30):
    """
    Returns latest articles for each team category in the latest_research_categories,
    grouped by team category. Uses the team_categories M2M relationship populated
    by rebuild_categories, so only articles whose titles/summaries matched the
    category's terms are included.
    
    Args:
        lst: Lists object
        days: Number of days to look back (default: 30)
        
    Returns:
        dict: Dictionary with team categories as keys and lists of articles as values
             Each category will have a maximum of 20 articles
    """
    result = {}
    
    # Check if the list has any latest research categories
    if not lst.latest_research_categories.exists():
        return result
    
    # Get articles for each team category via the team_categories M2M
    for category in lst.latest_research_categories.all():
        latest_articles = category.articles.filter(
            discovery_date__gte=now() - timedelta(days=days)
        ).order_by('-discovery_date').distinct()[:20]
        
        if latest_articles.exists():
            result[category] = latest_articles
    
    return result