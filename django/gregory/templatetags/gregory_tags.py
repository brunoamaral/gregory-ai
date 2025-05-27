from django import template

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
