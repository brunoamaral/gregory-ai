"""
Advanced content organization and personalization for email templates.
This module provides smart sorting, filtering, and content selection algorithms
for different email types and subscriber preferences.
"""

from django.utils import timezone
from gregory.models import Articles, Trials
from sitesettings.utils import author_page_base
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

	def __init__(self, email_type="weekly_summary"):
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
		if hasattr(articles, "exists"):
			has_articles = articles.exists()
		else:
			has_articles = bool(articles)

		if not has_articles:
			return {
				"featured_articles": [],
				"regular_articles": [],
				"total_count": 0,
				"high_confidence_count": 0,
			}

		# Apply email-type specific organization
		if self.email_type == "weekly_summary":
			return self._organize_weekly_articles(articles, subscriber, list_obj)
		elif self.email_type == "admin_summary":
			return self._organize_admin_articles(articles, subscriber)
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
		if hasattr(trials, "exists"):
			has_trials = trials.exists()
		else:
			has_trials = bool(trials)

		if not has_trials:
			return {
				"featured_trials": [],
				"regular_trials": [],
				"total_count": 0,
				"recruitment_count": 0,
			}

		# Sort by discovery date and status
		if hasattr(trials, "order_by"):
			organized_trials = list(trials.order_by("-discovery_date"))
		else:
			organized_trials = sorted(
				trials, key=lambda x: x.discovery_date, reverse=True
			)

		# Split on the normalized status. A NULL normalized status means the
		# normalizer did not recognise the raw value; treat that as not-recruiting
		# rather than falling back to a substring match on the raw string, which is
		# what produced the original bug (e.g. matching "Not Recruiting").
		recruiting_trials = [
			t
			for t in organized_trials
			if t.recruitment_status_normalized == "recruiting"
		]
		other_trials = [
			t
			for t in organized_trials
			if t.recruitment_status_normalized != "recruiting"
		]

		# Include ALL trials - don't limit content for subscribers
		featured_trials = recruiting_trials  # All recruiting trials
		regular_trials = other_trials  # All other trials

		return {
			"featured_trials": featured_trials,
			"regular_trials": regular_trials,
			"total_count": len(organized_trials),
			"recruitment_count": len(recruiting_trials),
		}

	def _organize_weekly_articles(self, articles, subscriber, list_obj):
		"""Organize articles for weekly summary emails: a single flat list,
		no featured/regular split.

		Neither `weekly_summary.html` nor the `additional_articles` loop it
		shares with `articles` draws any visual distinction between the two
		buckets the split used to produce — the split existed only to
		reorder articles, at the cost of a per-article manual-review/ML query
		(see docs/subscriptions.md). Selection (which articles qualify at
		all) already happened before this method runs; the command's own
		priority ranking (manual review + ML consensus, `send_weekly_summary`)
		decides order when `article_limit` truncation applies. Absent
		truncation, sort by discovery date — the same behavior 'date' sort
		order already had.
		"""
		if hasattr(articles, "order_by"):
			sorted_list = list(articles.order_by("-discovery_date"))
		else:
			sorted_list = sorted(articles, key=lambda x: x.discovery_date, reverse=True)

		if subscriber and list_obj:
			sorted_list = self._apply_subscriber_preferences(
				sorted_list, subscriber, list_obj
			)

		return {
			"featured_articles": [],
			"regular_articles": sorted_list,
			"total_count": len(sorted_list),
			"high_confidence_count": 0,
		}

	def _organize_admin_articles(self, articles, subscriber):
		"""Organize articles for admin summary emails."""
		# Sort by ML score for admin review
		sorted_articles = self._sort_by_ml_score(articles)

		# Split into high-confidence and needs review
		high_confidence = [
			a
			for a in sorted_articles
			if self._get_max_ml_score(a) > self.confidence_threshold
		]
		needs_review = [
			a
			for a in sorted_articles
			if self._get_max_ml_score(a) <= self.confidence_threshold
		]

		return {
			"featured_articles": high_confidence,  # All high-confidence articles for admin review
			"regular_articles": needs_review,  # All articles needing review
			"total_count": len(sorted_articles),
			"high_confidence_count": len(high_confidence),
		}

	def _organize_default_articles(self, articles):
		"""Default organization for unknown email types."""
		if hasattr(articles, "order_by"):
			sorted_articles = list(articles.order_by("-discovery_date"))
		else:
			sorted_articles = sorted(
				articles, key=lambda x: x.discovery_date, reverse=True
			)

		return {
			"featured_articles": sorted_articles,  # Include ALL articles - no limits
			"regular_articles": [],  # No need to split when including all
			"total_count": len(sorted_articles),
			"high_confidence_count": 0,
		}

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
		if hasattr(article, "filtered_ml_predictions"):
			if not article.filtered_ml_predictions:
				return 0.0

			max_score = 0.0
			for prediction in article.filtered_ml_predictions:
				if (
					hasattr(prediction, "probability_score")
					and prediction.probability_score
				):
					max_score = max(max_score, prediction.probability_score)

			return max_score

		# Fall back to standard predictions
		if (
			not hasattr(article, "ml_predictions_detail")
			or not article.ml_predictions_detail.exists()
		):
			return 0.0

		max_score = 0.0
		for prediction in article.ml_predictions_detail.all():
			if (
				hasattr(prediction, "probability_score")
				and prediction.probability_score
			):
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
		displayed_trials_count = len(trials.get("featured_trials", [])) + len(
			trials.get("regular_trials", [])
		)

		return {
			"total_articles": articles.get("total_count", 0),
			"high_confidence_articles": articles.get("high_confidence_count", 0),
			"featured_articles": len(articles.get("featured_articles", [])),
			"total_trials": displayed_trials_count,  # Show count of displayed trials, not all processed trials
			"all_trials_processed": trials.get(
				"total_count", 0
			),  # Keep total for reference if needed
			"recruiting_trials": trials.get("recruitment_count", 0),
			"featured_trials": len(trials.get("featured_trials", [])),
			"confidence_rate": (
				articles.get("high_confidence_count", 0)
				/ articles.get("total_count", 1)
				* 100
				if articles.get("total_count", 0) > 0
				else 0
			),
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
				"has_latest_research": False,
				"categories": [],
				"total_categories": 0,
				"total_articles": 0,
			}

		organized_categories = []
		total_articles = 0

		# Sort categories alphabetically by name
		for category, articles in sorted(
			category_articles_dict.items(), key=lambda x: x[0].category_name
		):
			# Sort articles by discovery date (newest first)
			sorted_articles = sorted(
				articles, key=lambda x: x.discovery_date, reverse=True
			)
			total_articles += len(sorted_articles)

			# Add to organized categories
			organized_categories.append(
				{
					"category": category,
					"category_name": category.category_name,
					"articles": sorted_articles,
					"article_count": len(sorted_articles),
				}
			)

		return {
			"has_latest_research": True,
			"categories": organized_categories,
			"total_categories": len(organized_categories),
			"total_articles": total_articles,
		}


class EmailRenderingPipeline:
	"""
	Optimized email rendering pipeline with performance enhancements
	and template caching capabilities.
	"""

	def __init__(self):
		self.organizer = EmailContentOrganizer()
		self.cache_enabled = True

	def prepare_optimized_context(
		self,
		email_type,
		articles=None,
		trials=None,
		subscriber=None,
		list_obj=None,
		site=None,
		custom_settings=None,
		confidence_threshold=None,
		utm_params=None,
		organization=None,
		latest_research_category_map=None,
	):
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
		    latest_research_category_map: dict of {TeamCategory: [Articles]} for the
		        weekly digest's Latest Research section, already filtered by the
		        caller (sent-record exclusion, lookback window, dedup against the
		        main article list). This method only formats it — see
		        send_weekly_summary for the filtering logic.

		Returns:
		    dict: Optimized context for template rendering
		"""
		# Initialize organizer for this email type
		self.organizer.email_type = email_type

		# Set custom confidence threshold if provided
		if confidence_threshold is not None:
			self.organizer.confidence_threshold = confidence_threshold

		# Optimize database queries with prefetch_related. Callers always pass
		# either an unsliced QuerySet or an already-materialized list (never a
		# sliced-but-still-QuerySet), so this never raises — see
		# send_weekly_summary/send_admin_summary/send_trials_notification,
		# which convert to a list before truncating.
		if articles is not None and hasattr(articles, "prefetch_related"):
			articles = articles.prefetch_related("authors")

		if trials is not None and hasattr(trials, "select_related"):
			trials = trials.select_related()

		# Organize content
		organized_articles = self.organizer.organize_articles(
			articles or Articles.objects.none(), subscriber, list_obj
		)

		organized_trials = self.organizer.organize_trials(
			trials or Trials.objects.none(), subscriber, list_obj
		)

		# Generate content statistics
		content_stats = self.organizer.get_content_statistics(
			organized_articles, organized_trials
		)

		# Build optimized context
		# Derive site domain for URL fallbacks from the site linked to the list.
		# Strip whitespace to guard against accidental spaces in Site.domain.
		_site_domain = site.domain.strip() if site and site.domain else ""
		_site_scheme = "http" if _site_domain in ("localhost", "127.0.0.1") else "https"
		_site_url_base = f"{_site_scheme}://{_site_domain}" if _site_domain else ""

		context = {
			"email_type": email_type,
			"current_date": timezone.now(),
			"subscriber": subscriber,
			"site": site,
			"custom_settings": custom_settings,
			"customsettings": custom_settings,  # Template compatibility
			# Site domain for URL construction
			"site_domain": _site_domain,
			# Base URL for author profile pages when this site publishes them,
			# else "" (templates fall back to orcid.org).
			"author_page_base": author_page_base(site, custom_settings),
			# UTM parameters for link tracking
			"utm_params": utm_params or {},
			# Footer context from CustomSetting, falling back to site domain when not set.
			# This ensures footer links always reflect the domain the list is linked to.
			"website_url": getattr(custom_settings, "website_url", "")
			or _site_url_base,
			"support_url": getattr(custom_settings, "support_url", ""),
			"about_url": getattr(custom_settings, "about_url", ""),
			"contact_url": getattr(custom_settings, "contact_url", ""),
			"bluesky_url": getattr(custom_settings, "bluesky_url", ""),
			"github_url": getattr(custom_settings, "github_url", ""),
			"mastodon_url": getattr(custom_settings, "mastodon_url", ""),
			"privacy_policy_url": getattr(custom_settings, "privacy_policy_url", ""),
			"terms_url": getattr(custom_settings, "terms_url", ""),
			# Organized content
			"articles": organized_articles.get("featured_articles", []),
			"additional_articles": organized_articles.get("regular_articles", []),
			"trials": organized_trials.get("featured_trials", []),
			"additional_trials": organized_trials.get("regular_trials", []),
			# Content statistics for smart template logic
			"content_stats": content_stats,
			"has_high_confidence_articles": content_stats["high_confidence_articles"]
			> 0,
			"has_recruiting_trials": content_stats["recruiting_trials"] > 0,
			# Performance metadata
			"render_timestamp": timezone.now(),
			"optimization_enabled": True,
		}

		# Add email-type specific context
		if email_type == "weekly_summary":
			# Latest Research formatting only — the caller (send_weekly_summary)
			# owns the filtering: sent-record exclusion, lookback window, and
			# dedup against the main article list.
			latest_research = {}
			if latest_research_category_map:
				latest_research = self.organizer.organize_latest_research_by_category(
					latest_research_category_map
				)

			context.update(
				{
					"greeting_time": self._get_greeting_time(),
					"user": subscriber,
					"list": list_obj,
					"title": getattr(custom_settings, "title", "Gregory AI"),
					"latest_research": latest_research,
				}
			)

		elif email_type == "admin_summary":
			# Handle both dict and object types for subscriber
			if isinstance(subscriber, dict):
				admin_email = subscriber.get("email", "admin@example.com")
			else:
				admin_email = getattr(subscriber, "email", "admin@example.com")

			context.update(
				{
					"admin": admin_email,
					"now": timezone.now(),
					"list": list_obj,
					"title": getattr(custom_settings, "title", "Gregory AI"),
					"show_ml_predictions": True,
					"show_admin_links": True,
				}
			)

		elif email_type == "trial_notification":
			context.update(
				{
					"now": timezone.now(),
					"title": getattr(custom_settings, "title", "Gregory AI"),
					"notification_type": "trial_update",
				}
			)

		logger.info(
			f"Optimized context prepared for {email_type}: "
			f"{content_stats['total_articles']} articles, "
			f"{content_stats['total_trials']} trials"
		)

		# Build per-org content map for email templates
		if organization is not None:
			from gregory.models import ArticleOrgContent

			all_email_articles = list(context.get("articles", [])) + list(
				context.get("additional_articles", [])
			)
			for category_data in context.get("latest_research", {}).get(
				"categories", []
			):
				all_email_articles.extend(category_data.get("articles", []))
			article_ids = [
				a.article_id for a in all_email_articles if hasattr(a, "article_id")
			]
			org_contents = {
				oc.article_id: oc
				for oc in ArticleOrgContent.objects.filter(
					article_id__in=article_ids,
					organization=organization,
				)
			}
			context["org_content_map"] = org_contents
		else:
			_ORG_EXPECTED_TYPES = {
				"weekly_summary",
				"admin_summary",
				"trial_notification",
			}
			if email_type in _ORG_EXPECTED_TYPES:
				logger.warning(
					"prepare_optimized_context called without organization for email_type=%s; "
					"org_content_map will be empty. Pass organization= for team-owned emails.",
					email_type,
				)
			context["org_content_map"] = {}

		return context

	def _get_greeting_time(self):
		"""Get appropriate greeting based on current time."""
		current_hour = timezone.now().hour
		if current_hour < 12:
			return "morning"
		elif current_hour < 17:
			return "afternoon"
		else:
			return "evening"


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
