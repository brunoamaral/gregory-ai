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
