from datetime import timedelta
from django.utils.timezone import now
from django.core.management.base import BaseCommand
from django.template.loader import get_template
from django.utils.html import strip_tags
from django.contrib.sites.models import Site
from subscriptions.management.commands.utils.send_email import send_email
from subscriptions.management.commands.utils.subscription import get_trials_for_list
from sitesettings.models import CustomSetting
from subscriptions.models import Lists, Subscribers, SentTrialNotification, FailedNotification
from gregory.models import TeamCredentials


class Command(BaseCommand):
	help = 'Sends real-time notifications for new clinical trials to subscribers, filtered by subjects, without relying on a sent flag on Trials.'

	def handle(self, *args, **options):
		customsettings = CustomSetting.objects.get(site=Site.objects.get_current().id)
		site = Site.objects.get_current()

		# Step 1: Find all lists that have subjects but are not weekly digests
		subject_lists = Lists.objects.filter(subjects__isnull=False, clinical_trials_notifications=True).distinct()

		if not subject_lists.exists():
			self.stdout.write(self.style.WARNING('No lists found with subjects.'))
			return

		threshold_date = now() - timedelta(days=30)  # Filter for the last 30 days

		for lst in subject_lists:
			# Fetch the team directly from the list
			team = lst.team  # This assumes Lists model has a ForeignKey to Team
			email_subject = lst.list_email_subject or f'There are new clinical trials for {lst.list_name}'

			if not team:
				self.stdout.write(self.style.ERROR(f"No team associated with list '{lst.list_name}'. Skipping."))
				continue

			# Step 2: Fetch Team Credentials
			try:
				credentials = team.credentials
				postmark_api_token = credentials.postmark_api_token
				api_url = credentials.postmark_api_url
			except TeamCredentials.DoesNotExist:
				self.stdout.write(self.style.ERROR(f"Credentials not found for team '{team.name}' associated with list '{lst.list_name}'. Skipping."))
				continue

			# Step 3: Use the shared utility function to fetch trials
			list_trials = get_trials_for_list(lst)

			if not list_trials.exists():
				self.stdout.write(self.style.WARNING(f'No trials found for the list "{lst.list_name}". Skipping.'))
				continue

			# Step 4: Find active subscribers for the list
			subscribers = Subscribers.objects.filter(
				active=True,
				subscriptions=lst
			).distinct()

			if not subscribers.exists():
				self.stdout.write(self.style.WARNING(f'No subscribers found for the list "{lst.list_name}".'))
				continue

			# Step 5: Notify each subscriber of new trials
			for subscriber in subscribers:
				# Determine which trials have already been sent to this subscriber for this list
				already_sent_ids = SentTrialNotification.objects.filter(
					trial__in=list_trials,
					list=lst,
					subscriber=subscriber,
					sent_at__gte=threshold_date  # Only notifications sent in the last 30 days
				).values_list('trial_id', flat=True)

				# Filter out already sent trials
				new_trials = list_trials.exclude(pk__in=already_sent_ids)

				if not new_trials.exists():
					self.stdout.write(self.style.WARNING(f'No new trials for {subscriber.email} in list "{lst.list_name}".'))
					continue

				# Step 6: Prepare and send the email
				summary_context = {
					"trials": new_trials,
					"title": customsettings.title,
					"email_footer": customsettings.email_footer,
					"site": site,
				}

				html_content = get_template('emails/trial_notification.html').render(summary_context)
				text_content = strip_tags(html_content)

				result = send_email(
					to=subscriber.email,
					subject=email_subject,
					html=html_content,
					text=text_content,
					site=site,
					sender_name="GregoryAI",
					api_token=postmark_api_token,  # Use the team's Postmark API token
					api_url=api_url
				)

				# Step 7: Parse the Postmark response
				if result.status_code == 200:
					response_data = result.json()
					error_code = response_data.get("ErrorCode", 0)
					message = response_data.get("Message", "Unknown error")

					if error_code == 0:  # Successful delivery
						self.stdout.write(self.style.SUCCESS(f"Email sent to {subscriber.email} for list '{lst.list_name}'."))
						# Record sent notifications for the new trials
						for trial in new_trials:
							SentTrialNotification.objects.get_or_create(trial=trial, list=lst, subscriber=subscriber)
					else:  # Failed delivery
						self.stdout.write(self.style.ERROR(f"Failed to send email to {subscriber.email} for list '{lst.list_name}'. Reason: {message}"))
						FailedNotification.objects.create(
							subscriber=subscriber,
							list=lst,
							reason=message
						)
				else:
					# Log generic failure if response status is not 200
					self.stdout.write(self.style.ERROR(f"Failed to send email to {subscriber.email} for list '{lst.list_name}'. Status: {result.status_code}"))
					FailedNotification.objects.create(
						subscriber=subscriber,
						list=lst,
						reason=f"HTTP Status {result.status_code}"
					)