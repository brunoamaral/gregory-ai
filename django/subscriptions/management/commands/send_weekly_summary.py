from django.core.management.base import BaseCommand
from django.template.loader import get_template
from django.utils.html import strip_tags
from django.contrib.sites.models import Site
from subscriptions.management.commands.utils.send_email import send_email
from subscriptions.management.commands.utils.subscription import (
	get_articles_for_list,
	get_trials_for_list,
)
from gregory.models import Articles, Trials, TeamCredentials
from sitesettings.models import CustomSetting
from subscriptions.models import (
	Lists,
	Subscribers,
	SentArticleNotification,
	SentTrialNotification,
	FailedNotification,
)


class Command(BaseCommand):
	help = 'Sends a weekly digest email for all weekly digest lists.'

	def handle(self, *args, **options):
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

			if not team:
				self.stdout.write(self.style.ERROR(f"No team associated with list '{digest_list.list_name}'. Skipping."))
				continue

			# Step 2: Fetch Team Credentials
			try:
				credentials = team.credentials
				postmark_api_token = credentials.postmark_api_token
			except TeamCredentials.DoesNotExist:
				self.stdout.write(self.style.ERROR(f"Credentials not found for team associated with list '{digest_list.list_name}'. Skipping."))
				continue

			# Step 3: Use utility functions to get articles and trials
			articles = get_articles_for_list(digest_list)
			trials = get_trials_for_list(digest_list)

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
				sent_article_ids = SentArticleNotification.objects.filter(
					article__in=articles,
					list=digest_list,
					subscriber=subscriber
				).values_list('article_id', flat=True)
				unsent_articles = articles.exclude(pk__in=sent_article_ids)

				sent_trial_ids = SentTrialNotification.objects.filter(
					trial__in=trials,
					list=digest_list,
					subscriber=subscriber
				).values_list('trial_id', flat=True)
				unsent_trials = trials.exclude(pk__in=sent_trial_ids)

				if not unsent_articles.exists() and not unsent_trials.exists():
					self.stdout.write(self.style.WARNING(f'No new articles or trials for {subscriber.email} in list "{digest_list.list_name}".'))
					continue

				# Step 6: Prepare and send the email
				summary_context = {
					"articles": unsent_articles,
					"trials": unsent_trials,
					"title": customsettings.title,
					"email_footer": customsettings.email_footer,
					"site": site,
				}

				html_content = get_template('emails/weekly_summary.html').render(summary_context)
				text_content = strip_tags(html_content)

				result = send_email(
					to=subscriber.email,
					subject=f'Your Weekly Digest: {digest_list.list_name}',
					html=html_content,
					text=text_content,
					site=site,
					sender_name="GregoryAI",
					api_token=postmark_api_token  # Use the team's Postmark API token
				)

				if result.status_code == 200:
					response_data = result.json()
					error_code = response_data.get("ErrorCode", 0)
					message = response_data.get("Message", "Unknown error")

					if error_code == 0:  # Successful delivery
						self.stdout.write(self.style.SUCCESS(f'Weekly digest email sent to {subscriber.email} for list "{digest_list.list_name}".'))
						# Record sent notifications
						for article in unsent_articles:
							SentArticleNotification.objects.get_or_create(
								article=article,
								list=digest_list,
								subscriber=subscriber
							)
						for trial in unsent_trials:
							SentTrialNotification.objects.get_or_create(
								trial=trial,
								list=digest_list,
								subscriber=subscriber
							)
					else:  # Failed delivery
						self.stdout.write(self.style.ERROR(f"Failed to send weekly digest email to {subscriber.email} for list '{digest_list.list_name}'. Reason: {message}"))
						FailedNotification.objects.create(
							subscriber=subscriber,
							list=digest_list,
							reason=message
						)
				else:
					# Log generic failure if response status is not 200
					self.stdout.write(self.style.ERROR(f"Failed to send weekly digest email to {subscriber.email} for list '{digest_list.list_name}'. Status: {result.status_code}"))
					FailedNotification.objects.create(
						subscriber=subscriber,
						list=digest_list,
						reason=f"HTTP Status {result.status_code}"
					)