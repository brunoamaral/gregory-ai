from django import template
from gregory.utils.text_utils import cleanHTML
from bs4 import BeautifulSoup
import re

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """
    Get an item from a dictionary safely
    """
    if dictionary is None:
        return None
    return dictionary.get(key)

@register.filter
def split(value, delimiter):
    """
    Split a string by a delimiter
    Usage: {{ email|split:"@"|first }}
    """
    if value is None:
        return []
    return str(value).split(delimiter)

@register.filter
def clean_html_tags(value):
    """
    Clean HTML and XML tags from text for email display.
    Removes tags like <div>, <jats:title>, <jats:sec>, etc.
    Usage: {{ article.summary|clean_html_tags|truncatechars:300 }}
    """
    if not value:
        return ""
    
    # Use BeautifulSoup to remove HTML/XML tags with proper spacing
    soup = BeautifulSoup(value, 'html.parser')
    cleaned = soup.get_text(separator=' ')
    
    # Additional regex cleanup for any remaining XML tags that might be missed
    # Remove JATS tags like <jats:title>, <jats:sec>, etc.
    cleaned = re.sub(r'<[^>]*jats:[^>]*>', ' ', cleaned)
    cleaned = re.sub(r'</[^>]*jats:[^>]*>', ' ', cleaned)
    
    # Remove any remaining XML/HTML tags
    cleaned = re.sub(r'<[^>]+>', ' ', cleaned)
    
    # Clean up extra whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned)
    cleaned = cleaned.strip()
    
    return cleaned

@register.filter
def format_subject(subject, format_type='short'):
    """
    Format a subject name based on the format type
    
    format_type options:
    - 'short': Just the subject name
    - 'team': Subject name [Team]
    - 'full': Subject name - Team (Organization)
    """
    if not subject:
        return ""
    
    if format_type == 'short':
        return subject.subject_name
    elif format_type == 'team':
        if subject.team:
            return f"{subject.subject_name} [{subject.team.name}]"
        return subject.subject_name
    elif format_type == 'full':
        return subject.get_full_name()
    else:
        # Default fallback
        return str(subject)

@register.filter
def format_team(team, format_type='default'):
    """
    Format a team name based on the format type
    
    format_type options:
    - 'default': Team name (with org name only if different)
    - 'short': Just the team name
    - 'full': Always Team name (Organization name)
    """
    if not team:
        return ""
    
    if format_type == 'short':
        return team.name
    elif format_type == 'full':
        if team.organization:
            return f"{team.name} ({team.organization.name})"
        return team.name
    else:  # default
        return str(team)

@register.filter
def has_attribute(obj, attribute):
    """
    Check if an object has a specific attribute
    Usage: {% if article|has_attribute:'filtered_ml_predictions' %}...{% endif %}
    """
    return hasattr(obj, attribute)

@register.filter
def add_utm_params(url, utm_params):
    """
    Add UTM parameters to a URL for tracking.
    Usage: {{ article.link|add_utm_params:utm_params }}
    
    Args:
        url: The URL to modify
        utm_params: Dictionary of UTM parameters
    
    Returns:
        URL with UTM parameters appended
    """
    if not url or not utm_params:
        return url
    
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
    
    # Parse the URL
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)
    
    # Add UTM parameters (don't override existing ones)
    for key, value in utm_params.items():
        if key not in query_params:
            query_params[key] = [value]
    
    # Rebuild the URL
    new_query = urlencode(query_params, doseq=True)
    new_url = urlunparse((
        parsed_url.scheme,
        parsed_url.netloc,
        parsed_url.path,
        parsed_url.params,
        new_query,
        parsed_url.fragment
    ))
    
    return new_url

@register.filter
def article_url(article):
    """
    Build a proper article URL using the current site domain.
    Usage: {{ article|article_url }}
    
    Args:
        article: Article object with article_id
    
    Returns:
        Full article URL using site domain
    """
    if not article or not hasattr(article, 'article_id') or not article.article_id:
        return ""
    
    from django.contrib.sites.models import Site
    
    try:
        site = Site.objects.get_current()
        # Fallback to gregory-ms.com if site domain is empty (same logic as send_weekly_summary.py)
        domain = site.domain if site.domain and site.domain.strip() else ''
    except:
        # Fallback if site framework fails
        domain = 'gregory-ms.com'
    
    return f"https://{domain}/articles/{article.article_id}/"
