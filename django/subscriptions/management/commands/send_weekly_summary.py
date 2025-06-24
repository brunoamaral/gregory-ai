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
	help = 'Sends a weekly digest email for all weekly digest lists.'
	
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

	def handle(self, *args, **options):
		threshold = options['threshold']
		days_to_look_back = options['days']
		debug = options['debug']

		
		if debug:
			self.stdout.write(self.style.NOTICE(f"Running with ML threshold: {threshold}, days: {days_to_look_back}"))
		
		site = Site.objects.get_current()
		customsettings = CustomSetting.objects.get(site=site)

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
			
			# Standard filtering: manually reviewed OR high ML prediction score
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
					self.stdout.write(self.style.NOTICE(f"  - Will include {unsent_articles.count()} new articles in the email"))
					self.stdout.write(self.style.NOTICE(f"  - Will include {unsent_trials.count()} new trials in the email"))

				if not unsent_articles.exists() and not unsent_trials.exists():
					self.stdout.write(self.style.WARNING(f'No new articles or trials for {subscriber.email} in list "{digest_list.list_name}".'))
					continue

				# Step 6: Prepare and send the email using optimized Phase 5 rendering pipeline
				summary_context = get_optimized_email_context(
					email_type='weekly_summary',
					articles=unsent_articles,
					trials=unsent_trials,
					subscriber=subscriber,
					list_obj=digest_list,
					site=site,
					custom_settings=customsettings,
					confidence_threshold=threshold  # Pass the threshold parameter to the content organizer
				)
				
				# Debug the final content that will appear in the email
				if debug:
					self.stdout.write(self.style.NOTICE(f"Final email content for subscriber {subscriber.email}:"))
					self.stdout.write(self.style.NOTICE(f"  - Featured Articles: {len(summary_context.get('articles', []))}"))
					self.stdout.write(self.style.NOTICE(f"  - Additional Articles: {len(summary_context.get('additional_articles', []))}"))
					self.stdout.write(self.style.NOTICE(f"  - Featured Trials: {len(summary_context.get('trials', []))}"))
					self.stdout.write(self.style.NOTICE(f"  - Additional Trials: {len(summary_context.get('additional_trials', []))}"))
					
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
						# Record sent notifications
						new_sent_count = 0
						for article in unsent_articles:
							SentArticleNotification.objects.get_or_create(
								article=article,
								list=digest_list,
								subscriber=subscriber
							)
							new_sent_count += 1
						self.stdout.write(self.style.NOTICE(f'  - Recorded {new_sent_count} new sent article notifications'))
						
						new_trial_sent_count = 0
						for trial in unsent_trials:
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