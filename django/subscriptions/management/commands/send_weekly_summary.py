from datetime import timedelta
from django.utils.timezone import now
from django.core.management.base import BaseCommand
from django.template.loader import get_template
from django.utils.html import strip_tags
from django.contrib.sites.models import Site
from subscriptions.management.commands.utils.send_email import send_email
from subscriptions.management.commands.utils.subscription import (
	get_articles_for_list,
	get_trials_for_list,
	get_latest_research_by_category,
)
from gregory.models import Articles, Authors, Trials, TeamCredentials, MLPredictions
from sitesettings.models import CustomSetting
from subscriptions.models import (
	Lists,
	Subscribers,
	SentArticleNotification,
	SentTrialNotification,
	FailedNotification,
)
from django.db.models import Q, Exists, OuterRef
from django.utils.timezone import now
from templates.emails.components.content_organizer import get_optimized_email_context

class Command(BaseCommand):
	help = '''Sends a weekly digest email for all weekly digest lists.
	
	Options:
	--threshold: ML prediction score threshold (default: 0.8)
	--days: Number of days to look back for articles (default: 30)
	--debug: Enable detailed debugging output
	--dry-run: Simulate sending emails without actually sending them
	--all-articles: Include all unsent articles regardless of ML predictions or manual review status, ordered by most recent
	'''
	
	def add_arguments(self, parser):
		parser.add_argument(
			'--threshold',
			type=float,
			default=0.8,
			help='ML prediction score threshold (default: 0.8)'
		)
		parser.add_argument(
			'--days',
			type=int,
			default=30,
			help='Number of days to look back for articles (default: 30)'
		)
		parser.add_argument(
			'--debug',
			action='store_true',
			help='Enable detailed debugging output'
		)
		parser.add_argument(
			'--dry-run',
			action='store_true',
			help='Simulate sending emails without actually sending them or recording sent notifications'
		)
		parser.add_argument(
			'--all-articles',
			action='store_true',
			help='Include all unsent articles regardless of ML predictions or manual review status, ordered by most recent'
		)

	def handle(self, *args, **options):
		threshold = options['threshold']
		days_to_look_back = options['days']
		debug = options['debug']
		dry_run = options['dry_run']
		all_articles = options['all_articles']

		if dry_run:
			self.stdout.write(self.style.WARNING("DRY RUN MODE: No emails will be sent and no records will be updated"))
		
		if all_articles:
			self.stdout.write(self.style.WARNING("ALL ARTICLES MODE: Including all unsent articles regardless of ML predictions or manual review"))
		
		if debug:
			self.stdout.write(self.style.NOTICE(f"Running with ML threshold: {threshold}, days: {days_to_look_back}, all_articles: {all_articles}"))
		
		site = Site.objects.get_current()
		customsettings = CustomSetting.objects.get(site=site)
		
		# Ensure site domain is not empty - fallback to a default if needed
		if not site.domain or site.domain.strip() == '':
			# Log the issue and use fallback domain
			self.stdout.write(self.style.WARNING(f"Site domain is empty! Using fallback domain 'gregory-ms.com'"))
			from types import SimpleNamespace
			site_with_domain = SimpleNamespace()
			site_with_domain.id = site.id
			site_with_domain.domain = 'gregory-ms.com'
			site_with_domain.name = site.name
			site = site_with_domain

		# Step 1: Find all lists that are weekly digests
		weekly_digest_lists = Lists.objects.filter(weekly_digest=True, subjects__isnull=False).distinct()

		if not weekly_digest_lists.exists():
			self.stdout.write(self.style.WARNING('No lists marked as weekly digest with subjects found.'))
			return

		for digest_list in weekly_digest_lists:
			# Fetch the team directly from the list
			team = digest_list.team  # Assumes Lists has a ForeignKey to Team
			email_subject = digest_list.list_email_subject or f'Your Weekly Digest: {digest_list.list_name}'
			if not team:
				self.stdout.write(self.style.ERROR(f"No team associated with list '{digest_list.list_name}'. Skipping."))
				continue

			# Step 2: Fetch Team Credentials
			try:
				credentials = team.credentials
				postmark_api_token = credentials.postmark_api_token
				api_url = credentials.postmark_api_url
			except TeamCredentials.DoesNotExist:
				self.stdout.write(self.style.ERROR(f"Credentials not found for team associated with list '{digest_list.list_name}'. Skipping."))
				continue

			# Step 3: Use utility functions to get articles and trials
			# Add verbose debugging to see how many articles are found
			self.stdout.write(self.style.NOTICE(f"Looking for articles for list '{digest_list.list_name}'..."))
			
			if all_articles:
				# When --all-articles flag is used, get all articles for the subjects regardless of ML predictions or manual review
				articles = Articles.objects.filter(
					subjects__in=digest_list.subjects.all(),
					discovery_date__gte=now() - timedelta(days=days_to_look_back)
				).distinct().order_by('-discovery_date')
				
				self.stdout.write(self.style.NOTICE(f"ALL ARTICLES MODE: Found {articles.count()} total articles (ordered by most recent)"))
				
			else:
				# Standard filtering: manually reviewed OR high ML prediction score
				# First, get articles by subject
				subject_articles = Articles.objects.filter(
					subjects__in=digest_list.subjects.all(),
					discovery_date__gte=now() - timedelta(days=days_to_look_back)
				).distinct()
				self.stdout.write(self.style.NOTICE(f"Found {subject_articles.count()} articles by subject"))
				
				# Then, get manually reviewed articles
				manual_reviewed = Articles.objects.filter(
					subjects__in=digest_list.subjects.all(),
					article_subject_relevances__subject__in=digest_list.subjects.all(),
					article_subject_relevances__is_relevant=True,
					discovery_date__gte=now() - timedelta(days=days_to_look_back)
				).distinct()
				self.stdout.write(self.style.NOTICE(f"Found {manual_reviewed.count()} manually reviewed articles"))
				
				# Get articles with ML prediction scores above threshold
				# Create a subquery to check for ML predictions above threshold
				ml_pred_subquery = MLPredictions.objects.filter(
					article=OuterRef('pk'),
					subject__in=digest_list.subjects.all(),
					probability_score__gte=threshold
				)
				
				# Get articles with valid ML predictions
				ml_predicted = Articles.objects.filter(
					subjects__in=digest_list.subjects.all(),
					discovery_date__gte=now() - timedelta(days=days_to_look_back)
				).filter(
					Exists(ml_pred_subquery)
				).distinct()
				self.stdout.write(self.style.NOTICE(f"Found {ml_predicted.count()} articles with ML prediction score ≥ {threshold}"))
				
				# Debugging: Check ML prediction scores for some articles
				if debug:
					sample_articles = subject_articles.order_by('-discovery_date')[:5]  # Get 5 most recent articles
					self.stdout.write(self.style.NOTICE(f"ML prediction scores for recent articles:"))
					for article in sample_articles:
						self.stdout.write(self.style.NOTICE(f"  Article {article.article_id}: {article.title[:50]}..."))
						ml_preds = article.ml_predictions_detail.all()
						if ml_preds.exists():
							for pred in ml_preds:
								self.stdout.write(self.style.NOTICE(f"    - Subject: {pred.subject.subject_name}, Score: {pred.probability_score}"))
						else:
							self.stdout.write(self.style.NOTICE(f"    - No ML predictions found"))
				
				# Combine manually reviewed OR high ML prediction score
				# Instead of union(), use a combined filter query
				article_ids = list(manual_reviewed.values_list('pk', flat=True)) + list(ml_predicted.values_list('pk', flat=True))
				articles = Articles.objects.filter(pk__in=article_ids).distinct()
				self.stdout.write(self.style.NOTICE(f"Filtered by manual review or ML threshold: {articles.count()} articles"))
				
				self.stdout.write(self.style.NOTICE(f"Final combined query found {articles.count()} articles"))
			
			# Use the helper function to get trials, but pass the days_to_look_back parameter
			trials = Trials.objects.filter(
				subjects__in=digest_list.subjects.all(),
				discovery_date__gte=now() - timedelta(days=days_to_look_back)
			).distinct()
			self.stdout.write(self.style.NOTICE(f"Found {trials.count()} trials"))

			if not articles.exists() and not trials.exists():
				self.stdout.write(self.style.WARNING(f'No articles or trials found for the weekly digest list "{digest_list.list_name}". Skipping.'))
				continue

			# Step 4: Find subscribers of the list
			subscribers = Subscribers.objects.filter(
				active=True,
				subscriptions=digest_list
			).distinct()

			if not subscribers.exists():
				self.stdout.write(self.style.WARNING(f'No active subscribers found for the weekly digest list "{digest_list.list_name}".'))
				continue

			for subscriber in subscribers:
				# Step 5: Filter unsent articles and trials for the subscriber
				threshold_date = now() - timedelta(days=30)
				sent_article_ids = SentArticleNotification.objects.filter(
					article__in=articles,
					list=digest_list,
					subscriber=subscriber,
					sent_at__gte=threshold_date
				).values_list('article_id', flat=True)
				unsent_articles = articles.exclude(pk__in=sent_article_ids)

				sent_trial_ids = SentTrialNotification.objects.filter(
					trial__in=trials,
					list=digest_list,
					subscriber=subscriber,
					sent_at__gte=threshold_date
				).values_list('trial_id', flat=True)
				unsent_trials = trials.exclude(pk__in=sent_trial_ids)
				
				# Add debugging for the filtered unsent articles
				if debug:
					self.stdout.write(self.style.NOTICE(f"For subscriber {subscriber.email}:"))
					self.stdout.write(self.style.NOTICE(f"  - Found {len(sent_article_ids)} already sent articles"))
					# Handle both QuerySet and list cases
					articles_count = len(unsent_articles) if isinstance(unsent_articles, list) else unsent_articles.count()
					trials_count = len(unsent_trials) if isinstance(unsent_trials, list) else unsent_trials.count()
					self.stdout.write(self.style.NOTICE(f"  - Will include {articles_count} new articles in the email"))
					self.stdout.write(self.style.NOTICE(f"  - Will include {trials_count} new trials in the email"))

				# Handle both QuerySet and list cases for existence check
				has_unsent_articles = bool(unsent_articles) if isinstance(unsent_articles, list) else unsent_articles.exists()
				has_unsent_trials = bool(unsent_trials) if isinstance(unsent_trials, list) else unsent_trials.exists()
				
				if not has_unsent_articles and not has_unsent_trials:
					self.stdout.write(self.style.WARNING(f'No new articles or trials for {subscriber.email} in list "{digest_list.list_name}".'))
					continue

				# Step 6: Apply article limit if specified in the subscription list
				article_limit = getattr(digest_list, 'article_limit', 15) or 15  # Default to 15 if not set or None
				# Handle both QuerySet and list cases
				articles_count = len(unsent_articles) if isinstance(unsent_articles, list) else unsent_articles.count()
				if articles_count > article_limit:
					if all_articles:
						# When using --all-articles, order by discovery date (newest first) only
						limited_articles = unsent_articles.order_by('-discovery_date')[:article_limit]
						if debug:
							self.stdout.write(self.style.NOTICE(f"Applied article limit in ALL ARTICLES mode: showing {article_limit} most recent articles out of {articles_count} available"))
					else:
						# Standard mode: Order by highest ML prediction score first, then by discovery date (newest first)
						# We need to annotate with the max ML prediction score for ordering
						from django.db.models import Max
						limited_articles = unsent_articles.annotate(
							max_ml_score=Max('ml_predictions_detail__probability_score')
						).order_by('-max_ml_score', '-discovery_date')[:article_limit]
						if debug:
							self.stdout.write(self.style.NOTICE(f"Applied article limit: showing {article_limit} highest-scoring articles (by ML prediction, then newest) out of {articles_count} available"))
					
					# Convert sliced QuerySet to list to avoid "Cannot filter a query once a slice has been taken" error
					unsent_articles = list(limited_articles)

				# Step 7: Prepare and send the email using optimized Phase 5 rendering pipeline
				# CRITICAL FIX: Get the organized content BEFORE recording as sent
				# This ensures what we record matches what gets sent
				
				# Prepare UTM parameters for tracking
				utm_params = {
					'utm_source': 'weekly_digest',
					'utm_medium': 'email',
					'utm_campaign': f'weekly_digest_{digest_list.list_name.lower().replace(" ", "_")}',
					'utm_content': f'subscriber_{subscriber.subscriber_id}'
				}
				
				summary_context = get_optimized_email_context(
					email_type='weekly_summary',
					articles=unsent_articles,
					trials=unsent_trials,
					subscriber=subscriber,
					list_obj=digest_list,
					site=site,
					custom_settings=customsettings,
					confidence_threshold=threshold,  # Pass the threshold parameter to the content organizer
					utm_params=utm_params  # Add UTM parameters to context
				)
				
				# Extract the actual articles that will be rendered in the email
				articles_to_be_sent = list(summary_context.get('articles', [])) + list(summary_context.get('additional_articles', []))
				trials_to_be_sent = list(summary_context.get('trials', [])) + list(summary_context.get('additional_trials', []))
				
				# Debug the final content that will appear in the email
				if debug:
					self.stdout.write(self.style.NOTICE(f"Final email content for subscriber {subscriber.email}:"))
					self.stdout.write(self.style.NOTICE(f"  - Featured Articles: {len(summary_context.get('articles', []))}"))
					self.stdout.write(self.style.NOTICE(f"  - Additional Articles: {len(summary_context.get('additional_articles', []))}"))
					self.stdout.write(self.style.NOTICE(f"  - Featured Trials: {len(summary_context.get('trials', []))}"))
					self.stdout.write(self.style.NOTICE(f"  - Additional Trials: {len(summary_context.get('additional_trials', []))}"))
					self.stdout.write(self.style.NOTICE(f"  - Total articles to be sent: {len(articles_to_be_sent)}"))
					self.stdout.write(self.style.NOTICE(f"  - Total trials to be sent: {len(trials_to_be_sent)}"))
					
					# Print actual article titles
					if summary_context.get('articles'):
						self.stdout.write(self.style.NOTICE("Featured article titles:"))
						for i, article in enumerate(summary_context.get('articles')):
							self.stdout.write(self.style.NOTICE(f"    {i+1}. {article.title[:50]}..."))
					
					if summary_context.get('additional_articles'):
						self.stdout.write(self.style.NOTICE("Additional article titles:"))
						for i, article in enumerate(summary_context.get('additional_articles')):
							self.stdout.write(self.style.NOTICE(f"    {i+1}. {article.title[:50]}..."))

				html_content = get_template('emails/weekly_summary.html').render(summary_context)
				text_content = strip_tags(html_content)
				
				# VERIFICATION: Check that the rendered HTML actually contains the articles
				if debug:
					# Count article titles in the rendered HTML
					article_count_in_html = 0
					missing_articles = []
					
					for article in articles_to_be_sent:
						# Try multiple ways to find the article in HTML
						title_found = False
						
						# Method 1: Exact title match (first 50 chars)
						if article.title[:50] in html_content:
							title_found = True
						
						# Method 2: Check for title without HTML tags (escape <scp> tags)
						import html
						clean_title = html.escape(article.title[:50])
						if clean_title in html_content:
							title_found = True
						
						# Method 3: Check for title with HTML entities decoded
						from django.utils.html import strip_tags as strip_html_tags
						stripped_title = strip_html_tags(article.title[:50])
						if stripped_title in html_content:
							title_found = True
						
						# Method 4: Check for partial matches (removing problematic characters)
						safe_title = article.title[:50].replace('<scp>', '').replace('</scp>', '').replace('‐', '-')
						if safe_title in html_content:
							title_found = True
						
						if title_found:
							article_count_in_html += 1
						else:
							missing_articles.append(article)
					
					self.stdout.write(self.style.NOTICE(f"VERIFICATION: {article_count_in_html} out of {len(articles_to_be_sent)} articles found in rendered HTML"))
					
					# If there's a mismatch, show which articles are missing
					if missing_articles:
						self.stdout.write(self.style.WARNING("MISMATCH DETECTED! Articles missing from HTML:"))
						for article in missing_articles:
							self.stdout.write(self.style.WARNING(f"  - MISSING: {article.title[:50]}..."))
							# Also show how the title appears in different formats
							import html
							self.stdout.write(self.style.WARNING(f"    * Original: {article.title[:50]}"))
							self.stdout.write(self.style.WARNING(f"    * HTML escaped: {html.escape(article.title[:50])}"))
							self.stdout.write(self.style.WARNING(f"    * Stripped: {strip_html_tags(article.title[:50])}"))
					
					# Also check for the "No New Content This Week" message
					if "No New Content This Week" in html_content:
						self.stdout.write(self.style.ERROR("WARNING: Email contains 'No New Content' message despite having articles!"))
					
					# Save the HTML content to a file for inspection
					import os
					debug_file = f"/tmp/weekly_summary_debug_{subscriber.subscriber_id}.html"
					with open(debug_file, 'w', encoding='utf-8') as f:
						f.write(html_content)
					self.stdout.write(self.style.NOTICE(f"HTML content saved to: {debug_file}"))

				if dry_run:
					# In dry-run mode, just log what would be sent without actually sending
					mode_info = "ALL ARTICLES mode" if all_articles else f"ML threshold {threshold} mode"
					self.stdout.write(self.style.SUCCESS(f'[DRY RUN] Would send weekly digest email to {subscriber.email} for list "{digest_list.list_name}" ({mode_info})'))
					self.stdout.write(self.style.NOTICE(f'  - Subject: {email_subject}'))
					# Show the actual articles that would be sent based on content organizer
					self.stdout.write(self.style.NOTICE(f'  - Would include {len(articles_to_be_sent)} articles and {len(trials_to_be_sent)} trials'))
					
					# Print more details if in debug mode
					if debug:
						self.stdout.write(self.style.NOTICE(f'  - Content summary:'))
						self.stdout.write(self.style.NOTICE(f'    * Featured Articles: {len(summary_context.get("articles", []))}'))
						self.stdout.write(self.style.NOTICE(f'    * Additional Articles: {len(summary_context.get("additional_articles", []))}'))
						self.stdout.write(self.style.NOTICE(f'    * Featured Trials: {len(summary_context.get("trials", []))}'))
						self.stdout.write(self.style.NOTICE(f'    * Additional Trials: {len(summary_context.get("additional_trials", []))}'))
					continue  # Skip to next subscriber without sending

				# If not in dry-run mode, proceed with actual sending
				result = send_email(
					to=subscriber.email,
					subject=email_subject,
					html=html_content,
					text=text_content,
					site=site,
					sender_name=customsettings.title,
					api_token=postmark_api_token,  # Use the team's Postmark API token
					api_url=api_url
				)

				if result.status_code == 200:
					response_data = result.json()
					error_code = response_data.get("ErrorCode", 0)
					message = response_data.get("Message", "Unknown error")

					if error_code == 0:  # Successful delivery
						self.stdout.write(self.style.SUCCESS(f'Weekly digest email sent to {subscriber.email} for list "{digest_list.list_name}".'))
						# Record sent notifications for articles that were actually sent in the email
						new_sent_count = 0
						for article in articles_to_be_sent:
							SentArticleNotification.objects.get_or_create(
								article=article,
								list=digest_list,
								subscriber=subscriber
							)
							new_sent_count += 1
						self.stdout.write(self.style.NOTICE(f'  - Recorded {new_sent_count} new sent article notifications (actually rendered in email)'))
						
						new_trial_sent_count = 0
						for trial in trials_to_be_sent:
							SentTrialNotification.objects.get_or_create(
								trial=trial,
								list=digest_list,
								subscriber=subscriber
							)
							new_trial_sent_count += 1
						self.stdout.write(self.style.NOTICE(f'  - Recorded {new_trial_sent_count} new sent trial notifications'))
					else:  # Failed delivery
						self.stdout.write(self.style.ERROR(f"Failed to send weekly digest email to {subscriber.email} for list '{digest_list.list_name}'. Reason: {message}"))
						FailedNotification.objects.create(
							subscriber=subscriber,
							list=digest_list,
							reason=message
						)
				else:
					# Enhanced error handling for non-200 status codes
					error_details = f"HTTP Status {result.status_code}"
					
					# For 422 errors, extract detailed Postmark error information
					if result.status_code == 422:
						try:
							error_response = result.json()
							error_code = error_response.get("ErrorCode", "Unknown")
							error_message = error_response.get("Message", "No details provided")
							error_details = f"422 Unprocessable Entity - ErrorCode: {error_code}, Message: {error_message}"
						except (ValueError, KeyError):
							error_details = f"422 Unprocessable Entity - Unable to parse error details"
					
					self.stdout.write(self.style.ERROR(f"Failed to send weekly digest email to {subscriber.email} for list '{digest_list.list_name}'. {error_details}"))
					FailedNotification.objects.create(
						subscriber=subscriber,
						list=digest_list,
						reason=error_details
					)