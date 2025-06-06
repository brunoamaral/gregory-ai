"""
Django views for email template rendering and preview functionality.
Provides endpoints for testing and previewing email templates with proper context.
"""

from django.http import HttpResponse, JsonResponse
from django.template.loader import get_template
from django.contrib.sites.models import Site
from django.views.decorators.http import require_http_methods
from django.shortcuts import render
from django.utils import timezone
from datetime import timedelta
import json

from sitesettings.models import CustomSetting
from gregory.models import Articles, Trials
from subscriptions.models import Lists, Subscribers
from templates.emails.components.context_helpers import (
    prepare_weekly_summary_context,
    prepare_admin_summary_context,
    prepare_trial_notification_context,
    sort_articles_by_ml_score,
    filter_high_confidence_articles,
    get_optimized_context_for_management_commands
)
from templates.emails.components.content_organizer import get_optimized_email_context


def email_preview_dashboard(request):
    """
    Dashboard for previewing email templates during development.
    """
    context = {
        'email_types': [
            ('weekly_summary', 'Weekly Summary'),
            ('admin_summary', 'Admin Summary'),
            ('trial_notification', 'Clinical Trials'),
            ('test_components', 'Component Test'),
        ]
    }
    return render(request, 'emails/email_preview.html', context)


@require_http_methods(["GET"])
def email_template_preview(request, template_name):
    """
    Render email templates with mock data for preview purposes.
    """
    # Get site and settings
    try:
        site = Site.objects.get_current()
        customsettings = CustomSetting.objects.get(site=site)
    except:
        # Fallback for development
        site = {'domain': 'gregory-ms.com', 'name': 'Gregory AI'}
        customsettings = {
            'title': 'Gregory AI - MS Research Updates',
            'email_footer': 'Thank you for using Gregory AI to stay updated on MS research.'
        }
    
    # Create mock subscriber
    mock_subscriber = {
        'email': 'preview@example.com',
        'first_name': 'Preview',
        'last_name': 'User'
    }
    
    # Get sample articles and trials (limit for preview)
    articles = Articles.objects.filter(
        discovery_date__gte=timezone.now() - timedelta(days=30)
    ).prefetch_related('authors', 'ml_predictions__subject').order_by('-discovery_date')[:5]
    
    trials = Trials.objects.filter(
        discovery_date__gte=timezone.now() - timedelta(days=30)
    ).order_by('-discovery_date')[:3]
    
    # Prepare context based on template type
    if template_name == 'weekly_summary':
        context = prepare_weekly_summary_context(
            articles=articles,
            trials=trials,
            subscriber=mock_subscriber,
            site=site,
            customsettings=customsettings
        )
        # Add user field for weekly summary compatibility
        context['user'] = mock_subscriber
        context['greeting_time'] = 'morning'
        
    elif template_name == 'admin_summary':
        context = prepare_admin_summary_context(
            articles=articles,
            trials=trials,
            subscriber=mock_subscriber,
            site=site,
            customsettings=customsettings
        )
        # Add now field for admin template
        context['now'] = timezone.now()
        
    elif template_name == 'trial_notification':
        context = prepare_trial_notification_context(
            trials=trials,
            subscriber=mock_subscriber,
            site=site,
            customsettings=customsettings
        )
        context['now'] = timezone.now()
        
    elif template_name == 'test_components':
        # Special template for testing components
        context = {
            'email_type': 'test',
            'current_date': timezone.now(),
            'site': site,
            'customsettings': customsettings,
            'subscriber': mock_subscriber,
            'articles': articles,
            'trials': trials,
            'now': timezone.now(),
            'title': customsettings.title if hasattr(customsettings, 'title') else 'Gregory AI',
            'email_footer': customsettings.email_footer if hasattr(customsettings, 'email_footer') else 'Gregory AI Footer'
        }
    else:
        return HttpResponse('Template not found', status=404)
    
    try:
        template = get_template(f'emails/{template_name}.html')
        rendered_email = template.render(context)
        return HttpResponse(rendered_email, content_type='text/html')
    except Exception as e:
        return HttpResponse(f'Error rendering template: {str(e)}', status=500)


@require_http_methods(["GET"])
def email_template_json_context(request, template_name):
    """
    Return the context data that would be used for a template as JSON.
    Useful for debugging template context issues.
    """
    # This is the same logic as email_template_preview but returns JSON
    try:
        site = Site.objects.get_current()
        customsettings = CustomSetting.objects.get(site=site)
    except:
        site = {'domain': 'gregory-ms.com', 'name': 'Gregory AI'}
        customsettings = {
            'title': 'Gregory AI - MS Research Updates',
            'email_footer': 'Thank you for using Gregory AI to stay updated on MS research.'
        }
    
    mock_subscriber = {
        'email': 'preview@example.com',
        'first_name': 'Preview',
        'last_name': 'User'
    }
    
    articles = Articles.objects.filter(
        discovery_date__gte=timezone.now() - timedelta(days=30)
    ).prefetch_related('authors', 'ml_predictions__subject').order_by('-discovery_date')[:5]
    
    trials = Trials.objects.filter(
        discovery_date__gte=timezone.now() - timedelta(days=30)
    ).order_by('-discovery_date')[:3]
    
    if template_name == 'weekly_summary':
        context = prepare_weekly_summary_context(
            articles=articles,
            trials=trials,
            subscriber=mock_subscriber,
            site=site,
            customsettings=customsettings
        )
    elif template_name == 'admin_summary':
        context = prepare_admin_summary_context(
            articles=articles,
            trials=trials,
            subscriber=mock_subscriber,
            site=site,
            customsettings=customsettings
        )
    elif template_name == 'trial_notification':
        context = prepare_trial_notification_context(
            trials=trials,
            subscriber=mock_subscriber,
            site=site,
            customsettings=customsettings
        )
    else:
        return JsonResponse({'error': 'Template not found'}, status=404)
    
    # Convert context to JSON-serializable format
    serializable_context = {}
    for key, value in context.items():
        if hasattr(value, '__dict__'):
            # Convert model instances to dict
            serializable_context[key] = str(value)
        elif hasattr(value, 'isoformat'):
            # Convert datetime objects
            serializable_context[key] = value.isoformat()
        else:
            serializable_context[key] = value
    
    return JsonResponse(serializable_context, json_dumps_params={'indent': 2})




def get_email_context_for_management_command(email_type, articles=None, trials=None, subscriber=None, site=None, customsettings=None):
    """
    Utility function for management commands to get properly formatted context.
    This bridges the gap between our new template system and existing management commands.
    
    Args:
        email_type (str): Type of email ('weekly_summary', 'admin_summary', 'trial_notification')
        articles: QuerySet of Article objects
        trials: QuerySet of Trial objects
        subscriber: Subscriber object
        site: Site object
        customsettings: CustomSetting object
    
    Returns:
        dict: Context dictionary ready for template rendering
    """
    
    if email_type == 'weekly_summary':
        context = prepare_weekly_summary_context(
            articles=articles or Articles.objects.none(),
            trials=trials or Trials.objects.none(), 
            subscriber=subscriber,
            site=site,
            customsettings=customsettings
        )
        # Add legacy field for compatibility
        context['user'] = subscriber
        context['greeting_time'] = 'morning'
        
    elif email_type == 'admin_summary':
        context = prepare_admin_summary_context(
            articles=articles or Articles.objects.none(),
            trials=trials or Trials.objects.none(),
            subscriber=subscriber, 
            site=site,
            customsettings=customsettings
        )
        context['now'] = timezone.now()
        
    elif email_type == 'trial_notification':
        context = prepare_trial_notification_context(
            trials=trials or Trials.objects.none(),
            subscriber=subscriber,
            site=site, 
            customsettings=customsettings
        )
        context['now'] = timezone.now()
        
    else:
        raise ValueError(f"Unknown email_type: {email_type}")
    
    return context


def prepare_email_context(email_type, articles=None, trials=None, subscriber=None, list_obj=None, site=None, custom_settings=None, admin_email=None):
    """
    Main function for preparing email context for management commands.
    This is the function imported by management commands.
    Enhanced with Phase 5 optimizations for better performance.
    
    Args:
        email_type (str): Type of email ('weekly_summary', 'admin_summary', 'trial_notification')
        articles: QuerySet of Article objects
        trials: QuerySet of Trial objects  
        subscriber: Subscriber object
        list_obj: Lists object (subscription list)
        site: Site object
        custom_settings: CustomSetting object
        admin_email: Admin email address (for admin summaries)
    
    Returns:
        dict: Context dictionary ready for template rendering
    """
    
    # Use the optimized Phase 5 email context preparation
    return get_optimized_email_context(
        email_type=email_type,
        articles=articles,
        trials=trials,
        subscriber=subscriber,
        list_obj=list_obj,
        site=site,
        custom_settings=custom_settings
    )


def sort_articles_for_email(articles, email_type='weekly_summary'):
    """
    Sort articles appropriately for different email types.
    
    Args:
        articles: QuerySet or list of Article objects
        email_type (str): Type of email to sort for
    
    Returns:
        list: Sorted articles
    """
    if email_type == 'admin_summary':
        # For admin emails, sort by ML score with high-confidence articles first
        return sort_articles_by_ml_score(articles)
    else:
        # For user emails, use discovery date order
        return list(articles.order_by('-discovery_date'))


def filter_articles_for_email(articles, email_type='weekly_summary', confidence_threshold=0.8):
    """
    Filter articles appropriately for different email types.
    
    Args:
        articles: QuerySet or list of Article objects
        email_type (str): Type of email to filter for
        confidence_threshold (float): ML confidence threshold for filtering
    
    Returns:
        list: Filtered articles
    """
    if email_type == 'weekly_summary':
        # For weekly summaries, only include high-confidence articles
        return filter_high_confidence_articles(articles, confidence_threshold)
    else:
        # For admin emails, include all articles for review
        return list(articles)
