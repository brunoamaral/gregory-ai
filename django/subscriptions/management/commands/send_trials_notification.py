from datetime import timedelta
from django.utils.timezone import now
from django.core.management.base import BaseCommand
from django.template.loader import get_template
from django.utils.html import strip_tags
from subscriptions.management.commands.utils.send_email import send_email
from subscriptions.management.commands.utils.subscription import get_trials_for_list
from subscriptions.models import (
	Lists,
	Subscribers,
	SentTrialNotification,
	FailedNotification,
)

from subscriptions.management.commands.utils.get_credentials import (
	build_unsubscribe_base_url,
	get_postmark_credentials,
	get_site_and_settings,
)
from templates.emails.components.content_organizer import get_optimized_email_context


class Command(BaseCommand):
	help = "Sends real-time notifications for new clinical trials to subscribers, filtered by subjects, without relying on a sent flag on Trials."

	def handle(self, *args, **options):
		# Initialize counters for summary
		emails_sent = 0
		emails_skipped = 0
		total_subscribers_processed = 0

		# Step 1: Find all lists that have subjects but are not weekly digests
		subject_lists = Lists.objects.filter(
			subjects__isnull=False, clinical_trials_notifications=True
		).distinct()

		if not subject_lists.exists():
			self.stdout.write(self.style.WARNING("No lists found with subjects."))
			return

		threshold_date = now() - timedelta(days=30)  # Filter for the last 30 days

		for lst in subject_lists:
			# Fetch the team directly from the list
			team = lst.team  # This assumes Lists model has a ForeignKey to Team
			email_subject = (
				lst.list_email_subject
				or f"There are new clinical trials for {lst.list_name}"
			)

			if not team:
				self.stdout.write(
					self.style.ERROR(
						f"No team associated with list '{lst.list_name}'. Skipping."
					)
				)
				continue

			# Step 2: Resolve site and custom settings for this list (List.site → Org default → global)
			try:
				site, customsettings = get_site_and_settings(team, list_obj=lst)
			except Exception as e:
				self.stdout.write(
					self.style.ERROR(
						f"Could not resolve site/settings for team '{team.name}': {e}. Skipping list '{lst.list_name}'."
					)
				)
				continue

			# Resolve Postmark credentials (Site-level CustomSetting → Organization → Django settings)
			postmark_api_token, api_url = get_postmark_credentials(
				custom_settings=customsettings, organization=team.organization
			)
			if not postmark_api_token or not api_url:
				self.stdout.write(
					self.style.ERROR(
						f"No Postmark credentials found for site, organisation, or Django settings. Skipping list '{lst.list_name}'."
					)
				)
				continue

			# Step 3: Use the shared utility function to fetch trials
			list_trials = get_trials_for_list(lst)

			if not list_trials.exists():
				self.stdout.write(
					self.style.WARNING(
						f'No trials found for the list "{lst.list_name}". Skipping.'
					)
				)
				continue

			# Step 4: Find active subscribers for the list (respect per-list opt-out)
			subscribers = Subscribers.objects.filter(
				active=True,
				list_subscriptions__list=lst,
				list_subscriptions__is_active=True,
			).distinct()

			if not subscribers.exists():
				self.stdout.write(
					self.style.WARNING(
						f'No subscribers found for the list "{lst.list_name}".'
					)
				)
				continue

			# Step 5: Notify each subscriber of new trials
			for subscriber in subscribers:
				total_subscribers_processed += 1
				# Determine which trials have already been sent to this subscriber for this list
				already_sent_ids = SentTrialNotification.objects.filter(
					trial__in=list_trials,
					list=lst,
					subscriber=subscriber,
					sent_at__gte=threshold_date,  # Only notifications sent in the last 30 days
				).values_list("trial_id", flat=True)

				# Filter out already sent trials
				new_trials = list_trials.exclude(pk__in=already_sent_ids)

				if not new_trials.exists():
					self.stdout.write(
						self.style.WARNING(
							f'No new trials for {subscriber.email} in list "{lst.list_name}". Skipping email.'
						)
					)
					emails_skipped += 1
					continue

				# Step 6: Prepare and send the email using optimized Phase 5 rendering pipeline
				summary_context = get_optimized_email_context(
					email_type="trial_notification",
					trials=new_trials,
					subscriber=subscriber,
					list_obj=lst,
					site=site,
					custom_settings=customsettings,
				)
				# Inject unsubscribe context for the footer template
				summary_context["list_id"] = lst.list_id
				summary_context["unsubscribe_base_url"] = build_unsubscribe_base_url(
					site, customsettings
				)
				summary_context["header_title"] = lst.header_title or ""
				summary_context["header_tagline"] = lst.header_tagline or ""
				summary_context["show_header_tagline"] = lst.show_header_tagline

				# Additional safety check: Ensure the context contains trials to display
				trials_in_context = summary_context.get("trials", [])
				additional_trials_in_context = summary_context.get(
					"additional_trials", []
				)

				if not trials_in_context and not additional_trials_in_context:
					self.stdout.write(
						self.style.WARNING(
							f'No trials to display in email context for {subscriber.email} in list "{lst.list_name}". Skipping email.'
						)
					)
					emails_skipped += 1
					continue

				html_content = get_template("emails/trial_notification.html").render(
					summary_context
				)
				text_content = strip_tags(html_content)

				result = send_email(
					to=subscriber.email,
					subject=email_subject,
					html=html_content,
					text=text_content,
					site=site,
					sender_name=customsettings.sender_name or customsettings.title,
					api_token=postmark_api_token,
					api_url=api_url,
					sender_prefix=customsettings.sender_email_prefix,
				)

				# Step 7: Parse the Postmark response
				if result.status_code == 200:
					response_data = result.json()
					error_code = response_data.get("ErrorCode", 0)
					message = response_data.get("Message", "Unknown error")

					if error_code == 0:  # Successful delivery
						self.stdout.write(
							self.style.SUCCESS(
								f"Email sent to {subscriber.email} for list '{lst.list_name}'."
							)
						)
						emails_sent += 1
						# Record sent notifications for the new trials
						for trial in new_trials:
							SentTrialNotification.objects.get_or_create(
								trial=trial, list=lst, subscriber=subscriber
							)
					else:  # Failed delivery
						self.stdout.write(
							self.style.ERROR(
								f"Failed to send email to {subscriber.email} for list '{lst.list_name}'. Reason: {message}"
							)
						)
						emails_skipped += 1
						FailedNotification.objects.create(
							subscriber=subscriber, list=lst, reason=message
						)
				else:
					# Enhanced error handling for non-200 status codes
					error_details = f"HTTP Status {result.status_code}"

					# For 422 errors, extract detailed Postmark error information
					if result.status_code == 422:
						try:
							error_response = result.json()
							error_code = error_response.get("ErrorCode", "Unknown")
							error_message = error_response.get(
								"Message", "No details provided"
							)
							error_details = f"422 Unprocessable Entity - ErrorCode: {error_code}, Message: {error_message}"
						except (ValueError, KeyError):
							error_details = f"422 Unprocessable Entity - Unable to parse error details"

					self.stdout.write(
						self.style.ERROR(
							f"Failed to send email to {subscriber.email} for list '{lst.list_name}'. {error_details}"
						)
					)
					emails_skipped += 1
					FailedNotification.objects.create(
						subscriber=subscriber, list=lst, reason=error_details
					)

		# Print summary
		self.stdout.write(self.style.SUCCESS(f"\nSummary:"))
		self.stdout.write(
			self.style.SUCCESS(
				f"Total subscribers processed: {total_subscribers_processed}"
			)
		)
		self.stdout.write(self.style.SUCCESS(f"Emails sent: {emails_sent}"))
		self.stdout.write(self.style.WARNING(f"Emails skipped: {emails_skipped}"))
		if emails_sent > 0:
			self.stdout.write(
				self.style.SUCCESS(f"✅ Trial notifications completed successfully!")
			)
		else:
			self.stdout.write(
				self.style.WARNING(
					f"ℹ️  No emails were sent (no new trials to notify about)."
				)
			)
