import logging
from datetime import timedelta
import requests
from django.utils.timezone import now
from django.core.management.base import BaseCommand
from django.template.loader import get_template
from subscriptions.management.commands.utils.send_email import (
	send_email,
	record_sent_message,
)
from subscriptions.management.commands.utils.subscription import get_trials_for_list
from subscriptions.models import (
	Lists,
	Subscribers,
	SentTrialNotification,
	FailedNotification,
	SuppressionEvent,
)

from subscriptions.management.commands.utils.get_credentials import (
	build_unsubscribe_base_url,
	get_postmark_credentials,
	get_site_and_settings,
)
from subscriptions.utils.email_limits import render_within_limit, resolve_limits
from subscriptions.utils.postmark import (
	POSTMARK_INACTIVE_RECIPIENT,
	classify_postmark_response,
)
from subscriptions.utils.suppression import deactivate_subscribers
from subscriptions.utils.utm import build_utm_params
from templates.emails.components.content_organizer import get_optimized_email_context

logger = logging.getLogger(__name__)


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

		for lst in subject_lists:
			# Sent-record exclusion window must be at least as wide as the
			# content window (lookback_days), or an item still inside the
			# content window but outside a fixed 30-day exclusion window gets
			# treated as unsent and re-mailed every run (audit finding 11).
			threshold_date = now() - timedelta(days=max(30, lst.lookback_days))
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
			list_trials = get_trials_for_list(lst, days=lst.lookback_days)

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
					sent_at__gte=threshold_date,  # Only notifications sent within the sent-record exclusion window
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

				# Step 6: Cap the number of trials so the rendered body can never
				# exceed Postmark's size limit (audit finding 1). Newest first;
				# whatever doesn't fit rolls over to the next run.
				_, trial_limit = resolve_limits(lst)
				new_trials = list(new_trials.order_by("-discovery_date")[:trial_limit])

				_context_holder = {}
				utm_params = build_utm_params("trial_notification", lst, "trial_card")

				def _render(articles, trials, _lst=lst, _subscriber=subscriber):
					summary_context = get_optimized_email_context(
						email_type="trial_notification",
						trials=trials,
						subscriber=_subscriber,
						list_obj=_lst,
						site=site,
						custom_settings=customsettings,
						organization=team.organization,
						utm_params=utm_params,
					)
					# Inject unsubscribe context for the footer template
					summary_context["list_id"] = _lst.list_id
					summary_context["unsubscribe_base_url"] = (
						build_unsubscribe_base_url(site, customsettings)
					)
					summary_context["header_title"] = _lst.header_title or ""
					summary_context["header_tagline"] = _lst.header_tagline or ""
					summary_context["show_header_tagline"] = _lst.show_header_tagline

					html = get_template("emails/trial_notification.html").render(
						summary_context
					)
					used_articles = list(summary_context.get("articles", [])) + list(
						summary_context.get("additional_articles", [])
					)
					used_trials = list(summary_context.get("trials", [])) + list(
						summary_context.get("additional_trials", [])
					)
					_context_holder["context"] = summary_context
					return html, used_articles, used_trials

				html_content, _articles_to_be_sent, trials_to_be_sent = (
					render_within_limit(_render, [], new_trials)
				)

				if html_content is None:
					reason = (
						f"Rendered trial notification for list '{lst.list_name}' "
						f"still exceeds the safe body size after shrinking to a "
						f"single trial."
					)
					logger.error(reason)
					FailedNotification.objects.create(
						subscriber=subscriber, list=lst, reason=reason
					)
					emails_skipped += 1
					continue

				if not trials_to_be_sent:
					self.stdout.write(
						self.style.WARNING(
							f'No trials to display in email context for {subscriber.email} in list "{lst.list_name}". Skipping email.'
						)
					)
					emails_skipped += 1
					continue

				text_content = get_template("emails/trial_notification.txt").render(
					_context_holder["context"]
				)

				try:
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
						tag="trial_notification",
					)
				except requests.RequestException as e:
					self.stdout.write(
						self.style.ERROR(
							f"Failed to send email to {subscriber.email} for list '{lst.list_name}'. Connection error: {e}"
						)
					)
					emails_skipped += 1
					FailedNotification.objects.create(
						subscriber=subscriber, list=lst, reason=f"Connection error: {e}"
					)
					record_sent_message(
						None,
						recipient=subscriber.email,
						subject=email_subject,
						tag="trial_notification",
						site=site,
						subscriber=subscriber,
					)
					continue

				record_sent_message(
					result,
					recipient=subscriber.email,
					subject=email_subject,
					tag="trial_notification",
					site=site,
					subscriber=subscriber,
				)

				# Step 7: Parse the Postmark response
				delivered, error_code, detail = classify_postmark_response(result)

				if delivered:
					self.stdout.write(
						self.style.SUCCESS(
							f"Email sent to {subscriber.email} for list '{lst.list_name}'."
						)
					)
					emails_sent += 1
					# Record sent notifications only for trials that were
					# actually rendered into the email (post-shrink).
					for trial in trials_to_be_sent:
						SentTrialNotification.objects.get_or_create(
							trial=trial, list=lst, subscriber=subscriber
						)
				elif error_code == POSTMARK_INACTIVE_RECIPIENT:
					logger.error(
						"Subscriber %s is suppressed at Postmark (list '%s'); "
						"deactivating globally — no further emails will be sent. %s",
						subscriber.email,
						lst.list_name,
						detail,
					)
					deactivate_subscribers(
						[subscriber.subscriber_id],
						reason=detail,
						record_type=SuppressionEvent.RECORD_TYPE_REACTIVE_SEND_FAILURE,
					)
					emails_skipped += 1
					FailedNotification.objects.create(
						subscriber=subscriber, list=lst, reason=detail
					)
				else:
					self.stdout.write(
						self.style.ERROR(
							f"Failed to send email to {subscriber.email} for list '{lst.list_name}'. {detail}"
						)
					)
					emails_skipped += 1
					FailedNotification.objects.create(
						subscriber=subscriber, list=lst, reason=detail
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
