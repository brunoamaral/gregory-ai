"""
Helper functions and context processors for email templates.
These functions help prepare the context data needed by the modular email components.
Enhanced with Phase 5 content organization and rendering pipeline optimization.
"""

from datetime import datetime, timedelta
from django.utils import timezone
from templates.emails.components.content_organizer import (
    EmailContentOrganizer,
    EmailRenderingPipeline,
    get_optimized_email_context,
    get_content_organizer
)


def get_greeting_time():
    """
    Get appropriate greeting based on current time.
    
    Returns:
        str: Time-appropriate greeting ('morning', 'afternoon', 'evening')
    """
    current_hour = timezone.now().hour
    
    if 5 <= current_hour < 12:
        return 'morning'
    elif 12 <= current_hour < 17:
        return 'afternoon'
    else:
        return 'evening'


def get_email_context(email_type, **kwargs):
    """
    Prepare standardized context data for email templates.
    
    Args:
        email_type (str): Type of email ('weekly_summary', 'admin_summary', 'trial_notification')
        **kwargs: Additional context data
    
    Returns:
        dict: Context dictionary with standardized variables
    """
    current_date = timezone.now()
    
    # Base context
    context = {
        'email_type': email_type,
        'current_date': current_date,
        'show_date': True,
    }
    
    # Add type-specific date ranges
    if email_type == 'weekly_summary':
        # Calculate date range for the past week
        end_date = current_date
        start_date = end_date - timedelta(days=7)
        context.update({
            'date_range_start': start_date,
            'date_range_end': end_date,
        })
    
    elif email_type == 'admin_summary':
        # Admin summaries cover last 48 hours
        context.update({
            'summary_period': '48 hours',
        })
    
    # Merge in any additional context
    context.update(kwargs)
    
    return context


def get_preheader_context(email_type, articles=None, trials=None):
    """
    Generate smart preheader text based on content.
    
    Args:
        email_type (str): Type of email
        articles (QuerySet): Articles to include in count
        trials (QuerySet): Trials to include in count
    
    Returns:
        dict: Context for preheader component
    """
    article_count = articles.count() if articles else 0
    trial_count = trials.count() if trials else 0
    
    return {
        'email_type': email_type,
        'articles': articles,
        'trials': trials,
        'article_count': article_count,
        'trial_count': trial_count,
    }


def prepare_weekly_summary_context(articles, trials, subscriber, site, customsettings):
    """
    Prepare context specifically for weekly summary emails.
    Enhanced with Phase 5 content organization.
    """
    # Use the optimized rendering pipeline for better performance
    pipeline = EmailRenderingPipeline()
    base_context = pipeline.prepare_optimized_context(
        email_type='weekly_summary',
        articles=articles,
        trials=trials,
        subscriber=subscriber,
        site=site,
        custom_settings=customsettings
    )
    
    # Add any weekly-specific enhancements
    base_context.update({
        'subscriber': subscriber,
        'title': customsettings.title,
        'email_footer': customsettings.email_footer,
        'greeting_time': get_greeting_time(),
    })
    
    return base_context


def prepare_admin_summary_context(articles, trials, subscriber, site, customsettings):
    """
    Prepare context specifically for admin summary emails.
    Enhanced with Phase 5 content organization and ML-based sorting.
    """
    # Use the optimized rendering pipeline for better performance
    pipeline = EmailRenderingPipeline()
    base_context = pipeline.prepare_optimized_context(
        email_type='admin_summary',
        articles=articles,
        trials=trials,
        subscriber=subscriber,
        site=site,
        custom_settings=customsettings
    )
    
    # Add admin-specific enhancements
    # Handle both dict and object types for subscriber
    if isinstance(subscriber, dict):
        admin_email = subscriber.get('email', 'admin@example.com')
    else:
        admin_email = getattr(subscriber, 'email', 'admin@example.com')
    
    base_context.update({
        'subscriber': subscriber,
        'admin': admin_email,
        'title': getattr(customsettings, 'title', 'Gregory AI') if hasattr(customsettings, 'title') else customsettings.get('title', 'Gregory AI'),
        'email_footer': getattr(customsettings, 'email_footer', '') if hasattr(customsettings, 'email_footer') else customsettings.get('email_footer', ''),
        'show_ml_predictions': True,
        'show_admin_links': True,
    })
    
    return base_context


def prepare_trial_notification_context(trials, subscriber, site, customsettings):
    """
    Prepare context specifically for clinical trial notification emails.
    Enhanced with Phase 5 content organization for high-confidence trials.
    """
    # Use the optimized rendering pipeline for better performance
    pipeline = EmailRenderingPipeline()
    base_context = pipeline.prepare_optimized_context(
        email_type='trial_notification',
        trials=trials,
        subscriber=subscriber,
        site=site,
        custom_settings=customsettings
    )
    
    # Add trial notification specific enhancements
    base_context.update({
        'subscriber': subscriber,
        'title': customsettings.title,
        'email_footer': customsettings.email_footer,
        'show_trial_details': True,
    })
    
    return base_context


def get_article_card_context(article, email_type='weekly_summary', site=None):
    """
    Prepare context for article card component based on email type.
    
    Args:
        article: Article model instance
        email_type (str): Type of email ('weekly_summary', 'admin_summary', 'trial_notification')
        site: Site model instance
    
    Returns:
        dict: Context for article card component
    """
    context = {
        'article': article,
        'site': site,
        'show_admin_links': email_type == 'admin_summary',
        'show_ml_predictions': email_type == 'admin_summary',
    }
    
    return context


def get_trial_card_context(trial, email_type='trial_notification', site=None):
    """
    Prepare context for trial card component based on email type.
    
    Args:
        trial: ClinicalTrial model instance
        email_type (str): Type of email ('weekly_summary', 'admin_summary', 'trial_notification')
        site: Site model instance
    
    Returns:
        dict: Context for trial card component
    """
    context = {
        'trial': trial,
        'site': site,
        'show_admin_links': email_type == 'admin_summary',
    }
    
    return context


def sort_articles_by_ml_score(articles):
    """
    Sort articles by highest ML prediction probability score.
    Articles with scores > 0.8 appear first.
    Enhanced with Phase 5 optimizations.
    
    Args:
        articles: QuerySet of Article objects
    
    Returns:
        list: Sorted list of articles
    """
    # Use the new content organizer for better performance
    organizer = get_content_organizer()
    return organizer.organize_articles(articles, email_type='admin_summary')


def filter_high_confidence_articles(articles, threshold=0.8):
    """
    Filter articles that have at least one ML prediction above threshold.
    Enhanced with Phase 5 optimizations.
    
    Args:
        articles: QuerySet or list of Article objects
        threshold (float): Minimum probability score to include
    
    Returns:
        list: Filtered list of high-confidence articles
    """
    # Use the new content organizer for consistent filtering
    organizer = get_content_organizer()
    return organizer._filter_high_confidence_articles(articles, threshold)


# Phase 5 Enhanced Helper Functions

def get_optimized_context_for_management_commands(email_type, articles=None, trials=None, subscriber=None, list_obj=None, site=None, custom_settings=None):
    """
    Optimized context preparation for management commands.
    Uses the Phase 5 EmailRenderingPipeline for better performance.
    
    Args:
        email_type (str): Type of email ('weekly_summary', 'admin_summary', 'trial_notification')
        articles: QuerySet of Article objects
        trials: QuerySet of Trial objects
        subscriber: Subscriber object
        list_obj: Lists object (subscription list)
        site: Site object
        custom_settings: CustomSetting object
    
    Returns:
        dict: Optimized context dictionary ready for template rendering
    """
    return get_optimized_email_context(
        email_type=email_type,
        articles=articles,
        trials=trials,
        subscriber=subscriber,
        list_obj=list_obj,
        site=site,
        custom_settings=custom_settings
    )


def organize_content_for_email(articles=None, trials=None, email_type='weekly_summary', subscriber=None):
    """
    Organize articles and trials for specific email types using Phase 5 content organization.
    
    Args:
        articles: QuerySet of Article objects
        trials: QuerySet of Trial objects
        email_type (str): Type of email to organize content for
        subscriber: Subscriber object for personalization
    
    Returns:
        dict: Dictionary with organized articles and trials
    """
    organizer = get_content_organizer()
    
    result = {}
    
    if articles is not None:
        result['articles'] = organizer.organize_articles(articles, email_type, subscriber)
        
    if trials is not None:
        result['trials'] = organizer.organize_trials(trials, email_type, subscriber)
    
    return result


def get_content_statistics(articles=None, trials=None):
    """
    Generate content statistics for smart template logic.
    
    Args:
        articles: QuerySet or list of Article objects
        trials: QuerySet or list of Trial objects
    
    Returns:
        dict: Statistics about the content
    """
    pipeline = EmailRenderingPipeline()
    return pipeline._generate_content_statistics(articles, trials)


def prepare_personalized_greeting(subscriber, email_type='weekly_summary'):
    """
    Prepare personalized greeting based on subscriber preferences and time.
    
    Args:
        subscriber: Subscriber object
        email_type (str): Type of email
    
    Returns:
        dict: Greeting context with personalized message
    """
    greeting_time = get_greeting_time()
    
    # Base greeting
    greeting = f"Good {greeting_time}"
    
    # Add subscriber name if available
    if hasattr(subscriber, 'first_name') and subscriber.first_name:
        greeting += f", {subscriber.first_name}"
    elif hasattr(subscriber, 'name') and subscriber.name:
        greeting += f", {subscriber.name}"
    
    return {
        'greeting': greeting,
        'greeting_time': greeting_time,
        'is_personalized': hasattr(subscriber, 'first_name') and bool(subscriber.first_name)
    }
