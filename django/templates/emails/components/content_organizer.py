"""
Advanced content organization and personalization for email templates.
This module provides smart sorting, filtering, and content selection algorithms
for different email types and subscriber preferences.
"""

from datetime import datetime, timedelta
from django.utils import timezone
from django.db.models import Q, Prefetch, F, Count, Avg
from gregory.models import Articles, Trials
import logging

logger = logging.getLogger(__name__)


class EmailContentOrganizer:
    """
    Advanced content organizer for email templates with smart sorting,
    filtering, and personalization capabilities.
    
    IMPORTANT: This organizer does NOT limit content by default - all relevant
    articles and trials are included to ensure subscribers receive complete
    information about research findings and clinical trial opportunities.
    """
    
    def __init__(self, email_type='weekly_summary'):
        self.email_type = email_type
        self.confidence_threshold = 0.8
        # Set to very high limits - we want to deliver ALL relevant content to subscribers
        self.max_articles_per_email = 999
        self.max_trials_per_email = 999
    
    def organize_articles(self, articles, subscriber=None, list_obj=None):
        """
        Organize articles with smart sorting and filtering based on email type.
        
        Args:
            articles: QuerySet of Article objects
            subscriber: Subscriber object for personalization
            list_obj: Lists object for subject filtering
        
        Returns:
            dict: Organized articles with metadata
        """
        # Handle both QuerySet and list cases
        if hasattr(articles, 'exists'):
            has_articles = articles.exists()
        else:
            has_articles = bool(articles)
        
        if not has_articles:
            return {
                'featured_articles': [],
                'regular_articles': [],
                'total_count': 0,
                'high_confidence_count': 0
            }
        
        # Apply email-type specific organization
        if self.email_type == 'weekly_summary':
            return self._organize_weekly_articles(articles, subscriber, list_obj)
        elif self.email_type == 'admin_summary':
            return self._organize_admin_articles(articles, subscriber)
        elif self.email_type == 'trial_notification':
            return self._organize_trial_notification_articles(articles, subscriber)
        else:
            return self._organize_default_articles(articles)
    
    def organize_trials(self, trials, subscriber=None, list_obj=None):
        """
        Organize clinical trials with smart sorting and filtering.
        
        Args:
            trials: QuerySet of Trial objects
            subscriber: Subscriber object for personalization
            list_obj: Lists object for subject filtering
        
        Returns:
            dict: Organized trials with metadata
        """
        # Handle both QuerySet and list cases
        if hasattr(trials, 'exists'):
            has_trials = trials.exists()
        else:
            has_trials = bool(trials)
        
        if not has_trials:
            return {
                'featured_trials': [],
                'regular_trials': [],
                'total_count': 0,
                'recruitment_count': 0
            }
        
        # Sort by discovery date and status
        organized_trials = list(trials.order_by('-discovery_date'))
        
        # Split into categories
        recruiting_trials = [t for t in organized_trials if t.recruitment_status and 'recruit' in str(t.recruitment_status).lower()]
        other_trials = [t for t in organized_trials if not t.recruitment_status or 'recruit' not in str(t.recruitment_status).lower()]
        
        # Include ALL trials - don't limit content for subscribers
        featured_trials = recruiting_trials  # All recruiting trials
        regular_trials = other_trials  # All other trials
        
        return {
            'featured_trials': featured_trials,
            'regular_trials': regular_trials,
            'total_count': len(organized_trials),
            'recruitment_count': len(recruiting_trials)
        }
    
    def _organize_weekly_articles(self, articles, subscriber, list_obj):
        """Organize articles for weekly summary emails."""
        # Get high-confidence articles first
        high_confidence_articles = self._filter_high_confidence(articles)
        
        # Handle both QuerySet and list cases for exclusion
        if hasattr(articles, 'exclude'):
            # QuerySet case - use exclude
            regular_articles = articles.exclude(
                pk__in=[a.pk for a in high_confidence_articles]
            )
        else:
            # List case - filter manually
            high_confidence_pks = [a.pk for a in high_confidence_articles]
            regular_articles = [a for a in articles if a.pk not in high_confidence_pks]
        
        # Sort by discovery date for user-friendly experience
        high_confidence_sorted = sorted(
            high_confidence_articles, 
            key=lambda x: x.discovery_date, 
            reverse=True
        )
        
        # Handle both QuerySet and list cases for ordering
        if hasattr(regular_articles, 'order_by'):
            # QuerySet case
            regular_sorted = list(regular_articles.order_by('-discovery_date'))
        else:
            # List case - sort manually
            regular_sorted = sorted(regular_articles, key=lambda x: x.discovery_date, reverse=True)
        
        # Apply subscriber preferences if available
        if subscriber and list_obj:
            high_confidence_sorted = self._apply_subscriber_preferences(
                high_confidence_sorted, subscriber, list_obj
            )
            regular_sorted = self._apply_subscriber_preferences(
                regular_sorted, subscriber, list_obj
            )
        
        # Include ALL articles - don't limit content for subscribers
        featured_articles = high_confidence_sorted  # All high-confidence articles
        regular_articles = regular_sorted  # All regular articles
        
        return {
            'featured_articles': featured_articles,
            'regular_articles': regular_articles,
            'total_count': len(high_confidence_sorted) + len(regular_sorted),
            'high_confidence_count': len(high_confidence_sorted)
        }
    
    def _organize_admin_articles(self, articles, subscriber):
        """Organize articles for admin summary emails."""
        # Sort by ML score for admin review
        sorted_articles = self._sort_by_ml_score(articles)
        
        # Split into high-confidence and needs review
        high_confidence = [a for a in sorted_articles if self._get_max_ml_score(a) > self.confidence_threshold]
        needs_review = [a for a in sorted_articles if self._get_max_ml_score(a) <= self.confidence_threshold]
        
        return {
            'featured_articles': high_confidence,  # All high-confidence articles for admin review
            'regular_articles': needs_review,      # All articles needing review
            'total_count': len(sorted_articles),
            'high_confidence_count': len(high_confidence)
        }
    
    def _organize_trial_notification_articles(self, articles, subscriber):
        """Organize articles for trial notification emails (minimal articles)."""
        # For trial notifications, only include highly relevant articles
        high_confidence = self._filter_high_confidence(articles)
        sorted_articles = sorted(high_confidence, key=lambda x: x.discovery_date, reverse=True)
        
        return {
            'featured_articles': sorted_articles,  # Include ALL high-confidence trial-related articles
            'regular_articles': [],
            'total_count': len(sorted_articles),
            'high_confidence_count': len(sorted_articles)
        }
    
    def _organize_default_articles(self, articles):
        """Default organization for unknown email types."""
        sorted_articles = list(articles.order_by('-discovery_date'))
        
        return {
            'featured_articles': sorted_articles,  # Include ALL articles - no limits
            'regular_articles': [],  # No need to split when including all
            'total_count': len(sorted_articles),
            'high_confidence_count': 0
        }
    
    def _filter_high_confidence(self, articles):
        """Filter articles with high ML confidence scores or manual review."""
        high_confidence = []
        
        for article in articles:
            # First check if the article was manually reviewed and marked as relevant
            if hasattr(article, 'article_subject_relevances') and article.article_subject_relevances.filter(is_relevant=True).exists():
                high_confidence.append(article)
                continue
                
            # Check for filtered predictions first (used by admin_summary)
            if hasattr(article, 'filtered_ml_predictions') and article.filtered_ml_predictions:
                max_score = self._get_max_ml_score(article)
                if max_score >= self.confidence_threshold:
                    high_confidence.append(article)
            # Fall back to standard predictions
            elif hasattr(article, 'ml_predictions_detail') and article.ml_predictions_detail.exists():
                max_score = self._get_max_ml_score(article)
                if max_score >= self.confidence_threshold:
                    high_confidence.append(article)
        
        return high_confidence
    
    def _sort_by_ml_score(self, articles):
        """Sort articles by highest ML prediction score."""
        def get_sort_key(article):
            ml_score = self._get_max_ml_score(article)
            # Secondary sort by discovery date for articles with same score
            return (ml_score, article.discovery_date)
        
        return sorted(list(articles), key=get_sort_key, reverse=True)
    
    def _get_max_ml_score(self, article):
        """Get the highest ML prediction score for an article."""
        # Check for filtered predictions first (used by admin_summary)
        if hasattr(article, 'filtered_ml_predictions'):
            if not article.filtered_ml_predictions:
                return 0.0
            
            max_score = 0.0
            for prediction in article.filtered_ml_predictions:
                if hasattr(prediction, 'probability_score') and prediction.probability_score:
                    max_score = max(max_score, prediction.probability_score)
            
            return max_score
        
        # Fall back to standard predictions
        if not hasattr(article, 'ml_predictions_detail') or not article.ml_predictions_detail.exists():
            return 0.0
        
        max_score = 0.0
        for prediction in article.ml_predictions_detail.all():
            if hasattr(prediction, 'probability_score') and prediction.probability_score:
                max_score = max(max_score, prediction.probability_score)
        
        return max_score
    
    def _apply_subscriber_preferences(self, articles, subscriber, list_obj):
        """Apply subscriber-specific content preferences."""
        # This can be enhanced with subscriber preference tracking
        # For now, maintain the existing order but could add:
        # - Reading history analysis
        # - Subject preference weighting
        # - Time-of-day preferences
        return articles
    
    def get_content_statistics(self, articles, trials):
        """
        Generate content statistics for email personalization.
        
        Args:
            articles: Organized articles
            trials: Organized trials
        
        Returns:
            dict: Content statistics for template context
        """
        # Calculate the actual number of trials that will be displayed
        displayed_trials_count = len(trials.get('featured_trials', [])) + len(trials.get('regular_trials', []))
        
        return {
            'total_articles': articles.get('total_count', 0),
            'high_confidence_articles': articles.get('high_confidence_count', 0),
            'featured_articles': len(articles.get('featured_articles', [])),
            'total_trials': displayed_trials_count,  # Show count of displayed trials, not all processed trials
            'all_trials_processed': trials.get('total_count', 0),  # Keep total for reference if needed
            'recruiting_trials': trials.get('recruitment_count', 0),
            'featured_trials': len(trials.get('featured_trials', [])),
            'confidence_rate': (
                articles.get('high_confidence_count', 0) / articles.get('total_count', 1) * 100
                if articles.get('total_count', 0) > 0 else 0
            )
        }
    
    def organize_latest_research_by_category(self, category_articles_dict):
        """
        Organize latest research articles by team category.
        
        Args:
            category_articles_dict: Dictionary with team categories as keys and lists of articles as values
            
        Returns:
            dict: Organized latest research articles with metadata
        """
        if not category_articles_dict:
            return {
                'has_latest_research': False,
                'categories': [],
                'total_categories': 0,
                'total_articles': 0
            }
        
        organized_categories = []
        total_articles = 0
        
        # Sort categories alphabetically by name
        for category, articles in sorted(category_articles_dict.items(), key=lambda x: x[0].category_name):
            # Sort articles by discovery date (newest first)
            sorted_articles = sorted(articles, key=lambda x: x.discovery_date, reverse=True)
            total_articles += len(sorted_articles)
            
            # Add to organized categories
            organized_categories.append({
                'category': category,
                'category_name': category.category_name,
                'articles': sorted_articles,
                'article_count': len(sorted_articles)
            })
        
        return {
            'has_latest_research': True,
            'categories': organized_categories,
            'total_categories': len(organized_categories),
            'total_articles': total_articles
        }


class EmailRenderingPipeline:
    """
    Optimized email rendering pipeline with performance enhancements
    and template caching capabilities.
    """
    
    def __init__(self):
        self.organizer = EmailContentOrganizer()
        self.cache_enabled = True
        
    def prepare_optimized_context(self, email_type, articles=None, trials=None, 
                                subscriber=None, list_obj=None, site=None, 
                                custom_settings=None, confidence_threshold=None, utm_params=None):
        """
        Prepare optimized context with content organization and performance enhancements.
        
        Args:
            email_type (str): Type of email being rendered
            articles: QuerySet of articles
            trials: QuerySet of trials  
            subscriber: Subscriber object
            list_obj: Lists object
            site: Site object
            custom_settings: CustomSetting object
            confidence_threshold: Custom ML prediction confidence threshold to use
            utm_params: Dictionary of UTM parameters for link tracking
            
        Returns:
            dict: Optimized context for template rendering
        """
        try:
            # Initialize organizer for this email type
            self.organizer.email_type = email_type
            
            # Set custom confidence threshold if provided
            if confidence_threshold is not None:
                self.organizer.confidence_threshold = confidence_threshold
            
            # Optimize database queries with prefetch_related
            if articles is not None and hasattr(articles, 'prefetch_related'):
                # Only apply prefetch if it's not already a sliced QuerySet
                try:
                    articles = articles.prefetch_related(
                        'authors',
                        'ml_predictions__subject'
                    )
                except Exception:
                    # If prefetch fails (e.g., already sliced), continue with original
                    pass
            
            if trials is not None and hasattr(trials, 'select_related'):
                # Only apply select_related if it's not already a sliced QuerySet
                try:
                    trials = trials.select_related()
                except Exception:
                    # If select_related fails (e.g., already sliced), continue with original
                    pass
            
            # Organize content
            organized_articles = self.organizer.organize_articles(
                articles or Articles.objects.none(), 
                subscriber, 
                list_obj
            )
            
            organized_trials = self.organizer.organize_trials(
                trials or Trials.objects.none(),
                subscriber,
                list_obj
            )
            
            # Generate content statistics
            content_stats = self.organizer.get_content_statistics(
                organized_articles, 
                organized_trials
            )
            
            # Build optimized context
            context = {
                'email_type': email_type,
                'current_date': timezone.now(),
                'subscriber': subscriber,
                'site': site,
                'custom_settings': custom_settings,
                'customsettings': custom_settings,  # Template compatibility
                
                # Site domain for URL construction (fallback logic same as send_weekly_summary.py)
                'site_domain': site.domain if site and site.domain and site.domain.strip() else 'gregory-ms.com',
                
                # UTM parameters for link tracking
                'utm_params': utm_params or {},
                
                # Organized content
                'articles': organized_articles.get('featured_articles', []),
                'additional_articles': organized_articles.get('regular_articles', []),
                'trials': organized_trials.get('featured_trials', []),
                'additional_trials': organized_trials.get('regular_trials', []),
                
                # Content statistics for smart template logic
                'content_stats': content_stats,
                'has_high_confidence_articles': content_stats['high_confidence_articles'] > 0,
                'has_recruiting_trials': content_stats['recruiting_trials'] > 0,
                
                # Performance metadata
                'render_timestamp': timezone.now(),
                'optimization_enabled': True
            }
            
            # Add email-type specific context
            if email_type == 'weekly_summary':
                # Add latest research by category if the list has any latest research categories
                latest_research = {}
                if list_obj and hasattr(list_obj, 'latest_research_categories'):
                    from subscriptions.management.commands.utils.subscription import get_latest_research_by_category
                    category_articles = get_latest_research_by_category(list_obj)
                    latest_research = self.organizer.organize_latest_research_by_category(category_articles)
                
                context.update({
                    'greeting_time': self._get_greeting_time(),
                    'user': subscriber,
                    'list': list_obj,
                    'title': getattr(custom_settings, 'title', 'Gregory AI'),
                    'email_footer': getattr(custom_settings, 'email_footer', ''),
                    'latest_research': latest_research
                })
            
            elif email_type == 'admin_summary':
                # Handle both dict and object types for subscriber
                if isinstance(subscriber, dict):
                    admin_email = subscriber.get('email', 'admin@example.com')
                else:
                    admin_email = getattr(subscriber, 'email', 'admin@example.com')
                
                context.update({
                    'admin': admin_email,
                    'now': timezone.now(),
                    'list': list_obj,
                    'title': getattr(custom_settings, 'title', 'Gregory AI'),
                    'email_footer': getattr(custom_settings, 'email_footer', ''),
                    'show_ml_predictions': True,
                    'show_admin_links': True
                })
            
            elif email_type == 'trial_notification':
                context.update({
                    'now': timezone.now(),
                    'title': getattr(custom_settings, 'title', 'Gregory AI'),
                    'email_footer': getattr(custom_settings, 'email_footer', ''),
                    'notification_type': 'trial_update'
                })
            
            logger.info(f"Optimized context prepared for {email_type}: "
                       f"{content_stats['total_articles']} articles, "
                       f"{content_stats['total_trials']} trials")
            
            return context
            
        except Exception as e:
            logger.error(f"Error preparing optimized context for {email_type}: {str(e)}")
            # Fallback to basic context
            return self._get_fallback_context(email_type, subscriber, site, custom_settings)
    
    def _get_greeting_time(self):
        """Get appropriate greeting based on current time."""
        current_hour = timezone.now().hour
        if current_hour < 12:
            return 'morning'
        elif current_hour < 17:
            return 'afternoon'
        else:
            return 'evening'
    
    def _get_fallback_context(self, email_type, subscriber, site, custom_settings):
        """Provide fallback context if optimization fails."""
        base_context = {
            'email_type': email_type,
            'current_date': timezone.now(),
            'subscriber': subscriber,
            'site': site,
            'customsettings': custom_settings,
            'articles': [],
            'trials': [],
            'content_stats': {
                'total_articles': 0,
                'total_trials': 0,
                'high_confidence_articles': 0,
                'recruiting_trials': 0
            },
            'optimization_enabled': False,
            'error_mode': True
        }
        
        # Add email-type specific context for fallback
        if email_type == 'weekly_summary':
            base_context.update({
                'greeting_time': 'morning',
                'user': subscriber,
                'title': getattr(custom_settings, 'title', 'Gregory AI'),
                'email_footer': getattr(custom_settings, 'email_footer', '')
            })
        
        elif email_type == 'admin_summary':
            # Handle both dict and object types for subscriber
            if isinstance(subscriber, dict):
                admin_email = subscriber.get('email', 'admin@example.com')
            else:
                admin_email = getattr(subscriber, 'email', 'admin@example.com')
            
            base_context.update({
                'admin': admin_email,
                'now': timezone.now(),
                'title': getattr(custom_settings, 'title', 'Gregory AI'),
                'email_footer': getattr(custom_settings, 'email_footer', ''),
                'show_ml_predictions': True,
                'show_admin_links': True
            })
        
        elif email_type == 'trial_notification':
            base_context.update({
                'now': timezone.now(),
                'title': getattr(custom_settings, 'title', 'Gregory AI'),
                'email_footer': getattr(custom_settings, 'email_footer', ''),
                'notification_type': 'trial_update'
            })
        
        return base_context


# Convenience functions for easy import by management commands
def get_optimized_email_context(email_type, **kwargs):
    """
    Convenience function to get optimized email context.
    This is the main function that should be used by management commands.
    """
    pipeline = EmailRenderingPipeline()
    return pipeline.prepare_optimized_context(email_type, **kwargs)


def get_content_organizer(email_type):
    """Get a content organizer instance for specific email type."""
    return EmailContentOrganizer(email_type)
