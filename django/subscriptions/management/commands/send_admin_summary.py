from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand
from django.template.loader import get_template
from django.utils.html import strip_tags
from gregory.models import Articles, Trials, MLPredictions, TeamCredentials
from sitesettings.models import CustomSetting
from subscriptions.management.commands.utils.send_email import send_email
from subscriptions.management.commands.utils.subscription import get_trials_for_list, get_articles_for_list
from subscriptions.models import Lists, Subscribers, SentArticleNotification, SentTrialNotification, FailedNotification
from django.db.models import Prefetch
from django.utils.timezone import now
from datetime import timedelta
from templates.emails.components.content_organizer import get_optimized_email_context


class Command(BaseCommand):
	help = 'Sends an admin summary every 2 days.'

	def handle(self, *args, **options):
		site = Site.objects.get_current()
		customsettings = CustomSetting.objects.get(site=site)

		# Step 1: Find all lists that are admin summaries
		admin_summary_lists = Lists.objects.filter(admin_summary=True).distinct()

		if not admin_summary_lists.exists():
			self.stdout.write(self.style.WARNING('No lists marked as admin summary found.'))
			return

		threshold_date = now() - timedelta(days=30)  # Filter for the last 30 days

		for admin_list in admin_summary_lists:
			# Fetch the team directly from the list
			team = admin_list.team
			email_subject = admin_list.list_email_subject or f'{admin_list.list_name} | Admin Summary'

			if not team:
				self.stdout.write(self.style.ERROR(f"No team associated with list '{admin_list.list_name}'. Skipping."))
				continue

			# Fetch Team Credentials
			try:
				credentials = team.credentials
				postmark_api_token = credentials.postmark_api_token
				api_url = credentials.postmark_api_url
			except TeamCredentials.DoesNotExist:
				self.stdout.write(self.style.ERROR(f"Credentials not found for team associated with list '{admin_list.list_name}'. Skipping."))
				continue

			# Step 2: Fetch articles and trials for this list
			# First, get the subjects associated with this list
			list_subjects = admin_list.subjects.all()
			
			# Fetch articles with ML predictions for the list's subjects only
			list_articles = get_articles_for_list(admin_list).prefetch_related(
				Prefetch(
					'ml_predictions_detail',
					queryset=MLPredictions.objects.filter(subject__in=list_subjects),
					to_attr='filtered_ml_predictions'
				)
			)
			
			list_trials = get_trials_for_list(admin_list)

			# Step 3: Find subscribers of the list
			subscribers = Subscribers.objects.filter(
				active=True,
				subscriptions=admin_list
			).distinct()

			if not subscribers.exists():
				self.stdout.write(self.style.WARNING(f'No active subscribers found for the admin summary list "{admin_list.list_name}".'))
				continue

			for subscriber in subscribers:
				# Determine which articles have already been sent to this subscriber for this list
				already_sent_article_ids = SentArticleNotification.objects.filter(
					article__in=list_articles,
					list=admin_list,
					subscriber=subscriber,
					sent_at__gte=threshold_date  # Only notifications sent in the last 30 days
				).values_list('article_id', flat=True)

				new_articles = list_articles.exclude(pk__in=already_sent_article_ids)

				# Determine which trials have already been sent to this subscriber for this list
				already_sent_trial_ids = SentTrialNotification.objects.filter(
					trial__in=list_trials,
					list=admin_list,
					subscriber=subscriber,
					sent_at__gte=threshold_date  # Only notifications sent in the last 30 days
				).values_list('trial_id', flat=True)

				new_trials = list_trials.exclude(pk__in=already_sent_trial_ids)

				if not new_articles.exists() and not new_trials.exists():
					self.stdout.write(self.style.WARNING(f"No new articles or trials to send to {subscriber.email}."))
					continue

				self.stdout.write(self.style.SUCCESS(f"Sending admin summary to {subscriber.email}."))

				# Step 4: Prepare the summary context for the email using optimized Phase 5 rendering pipeline
				summary_context = get_optimized_email_context(
					email_type='admin_summary',
					articles=new_articles,
					trials=new_trials,
					subscriber=subscriber,
					list_obj=admin_list,
					site=site,
					custom_settings=customsettings
				)

				# Render email content using new template
				html_content = get_template('emails/admin_summary.html').render(summary_context)
				text_content = strip_tags(html_content)

				# Step 5: Send email
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

				if result and result.status_code == 200:
					response_data = result.json()
					error_code = response_data.get("ErrorCode", 0)
					message = response_data.get("Message", "Unknown error")

					if error_code == 0:  # Successful delivery
						self.stdout.write(self.style.SUCCESS(f"Email sent to {subscriber.email} for list '{admin_list.list_name}'."))
						# Record sent notifications for the new articles
						for article in new_articles:
							SentArticleNotification.objects.get_or_create(article=article, list=admin_list, subscriber=subscriber)
						# Record sent notifications for the new trials
						for trial in new_trials:
							SentTrialNotification.objects.get_or_create(trial=trial, list=admin_list, subscriber=subscriber)
					else:
						self.stdout.write(self.style.ERROR(f"Failed to send email to {subscriber.email} for list '{admin_list.list_name}'. Reason: {message}"))
						FailedNotification.objects.create(
							subscriber=subscriber,
							list=admin_list,
							reason=message
						)
				else:
					# Enhanced error handling for non-200 status codes
					error_details = f"HTTP Status {result.status_code if result else 'No Response'}"
					
					# For 422 errors, extract detailed Postmark error information
					if result and result.status_code == 422:
						try:
							error_response = result.json()
							error_code = error_response.get("ErrorCode", "Unknown")
							error_message = error_response.get("Message", "No details provided")
							error_details = f"422 Unprocessable Entity - ErrorCode: {error_code}, Message: {error_message}"
						except (ValueError, KeyError):
							error_details = f"422 Unprocessable Entity - Unable to parse error details"
					
					self.stdout.write(self.style.ERROR(f"Failed to send email to {subscriber.email} for list '{admin_list.list_name}'. {error_details}"))
					FailedNotification.objects.create(
						subscriber=subscriber,
						list=admin_list,
						reason=error_details
					)