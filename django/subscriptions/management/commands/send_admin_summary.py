import logging
from django.core.management.base import BaseCommand
from django.template.loader import get_template
from django.utils.html import strip_tags
from gregory.models import MLPredictions
from subscriptions.management.commands.utils.get_credentials import (
	build_unsubscribe_base_url,
	get_postmark_credentials,
	get_site_and_settings,
)
from subscriptions.management.commands.utils.send_email import send_email
from subscriptions.management.commands.utils.subscription import (
	get_trials_for_list,
	get_articles_for_list,
)
from subscriptions.models import (
	Lists,
	Subscribers,
	SentArticleNotification,
	SentTrialNotification,
	FailedNotification,
)
from subscriptions.utils.email_limits import render_within_limit, resolve_limits
from django.db.models import Prefetch
from django.utils.timezone import now
from datetime import timedelta
from templates.emails.components.content_organizer import get_optimized_email_context

logger = logging.getLogger(__name__)


class Command(BaseCommand):
	help = "Sends an admin summary every 2 days."

	def handle(self, *args, **options):
		# Step 1: Find all lists that are admin summaries
		admin_summary_lists = Lists.objects.filter(admin_summary=True).distinct()

		if not admin_summary_lists.exists():
			self.stdout.write(
				self.style.WARNING("No lists marked as admin summary found.")
			)
			return

		threshold_date = now() - timedelta(days=30)  # Filter for the last 30 days

		for admin_list in admin_summary_lists:
			# Fetch the team directly from the list
			team = admin_list.team
			email_subject = (
				admin_list.list_email_subject
				or f"{admin_list.list_name} | Admin Summary"
			)

			if not team:
				self.stdout.write(
					self.style.ERROR(
						f"No team associated with list '{admin_list.list_name}'. Skipping."
					)
				)
				continue
			organization = team.organization

			# Resolve site and custom settings for this list (List.site → Org default → global)
			try:
				site, customsettings = get_site_and_settings(team, list_obj=admin_list)
			except Exception as e:
				self.stdout.write(
					self.style.ERROR(
						f"Could not resolve site/settings for team '{team.name}': {e}. Skipping list '{admin_list.list_name}'."
					)
				)
				continue

			# Resolve Postmark credentials (Site-level CustomSetting → Organization → Django settings)
			postmark_api_token, api_url = get_postmark_credentials(
				custom_settings=customsettings, organization=organization
			)
			if not postmark_api_token or not api_url:
				self.stdout.write(
					self.style.ERROR(
						f"No Postmark credentials found for site, organisation, or Django settings. Skipping list '{admin_list.list_name}'."
					)
				)
				continue

			# Step 2: Fetch articles and trials for this list
			# First, get the subjects associated with this list
			list_subjects = admin_list.subjects.all()

			# Fetch articles with ML predictions for the list's subjects only
			list_articles = get_articles_for_list(admin_list).prefetch_related(
				Prefetch(
					"ml_predictions_detail",
					queryset=MLPredictions.objects.filter(subject__in=list_subjects),
					to_attr="filtered_ml_predictions",
				)
			)

			list_trials = get_trials_for_list(admin_list)

			# Step 3: Find subscribers of the list (respect per-list opt-out)
			subscribers = Subscribers.objects.filter(
				active=True,
				list_subscriptions__list=admin_list,
				list_subscriptions__is_active=True,
			).distinct()

			if not subscribers.exists():
				self.stdout.write(
					self.style.WARNING(
						f'No active subscribers found for the admin summary list "{admin_list.list_name}".'
					)
				)
				continue

			for subscriber in subscribers:
				# Determine which articles have already been sent to this subscriber for this list
				already_sent_article_ids = SentArticleNotification.objects.filter(
					article__in=list_articles,
					list=admin_list,
					subscriber=subscriber,
					sent_at__gte=threshold_date,  # Only notifications sent in the last 30 days
				).values_list("article_id", flat=True)

				new_articles = list_articles.exclude(pk__in=already_sent_article_ids)

				# Determine which trials have already been sent to this subscriber for this list
				already_sent_trial_ids = SentTrialNotification.objects.filter(
					trial__in=list_trials,
					list=admin_list,
					subscriber=subscriber,
					sent_at__gte=threshold_date,  # Only notifications sent in the last 30 days
				).values_list("trial_id", flat=True)

				new_trials = list_trials.exclude(pk__in=already_sent_trial_ids)

				if not new_articles.exists() and not new_trials.exists():
					self.stdout.write(
						self.style.WARNING(
							f"No new articles or trials to send to {subscriber.email}."
						)
					)
					continue

				self.stdout.write(
					self.style.SUCCESS(f"Sending admin summary to {subscriber.email}.")
				)

				# Step 4: Cap articles/trials so the rendered body can never
				# exceed Postmark's size limit (audit finding 1). Ordered
				# newest-first so truncation is deterministic run to run;
				# whatever doesn't fit rolls over to the next send.
				article_limit, trial_limit = resolve_limits(admin_list)
				new_articles = list(
					new_articles.order_by("-discovery_date")[:article_limit]
				)
				new_trials = list(new_trials.order_by("-discovery_date")[:trial_limit])

				def _render(
					articles,
					trials,
					_admin_list=admin_list,
					_subscriber=subscriber,
				):
					summary_context = get_optimized_email_context(
						email_type="admin_summary",
						articles=articles,
						trials=trials,
						subscriber=_subscriber,
						list_obj=_admin_list,
						site=site,
						custom_settings=customsettings,
						organization=organization,
					)
					# Inject unsubscribe context for the footer template
					summary_context["list_id"] = _admin_list.list_id
					summary_context["unsubscribe_base_url"] = (
						build_unsubscribe_base_url(site, customsettings)
					)
					summary_context["header_title"] = _admin_list.header_title or ""
					summary_context["header_tagline"] = (
						_admin_list.header_tagline or ""
					)
					summary_context["show_header_tagline"] = (
						_admin_list.show_header_tagline
					)

					html = get_template("emails/admin_summary.html").render(
						summary_context
					)
					used_articles = list(
						summary_context.get("articles", [])
					) + list(summary_context.get("additional_articles", []))
					used_trials = list(summary_context.get("trials", [])) + list(
						summary_context.get("additional_trials", [])
					)
					return html, used_articles, used_trials

				html_content, articles_to_be_sent, trials_to_be_sent = (
					render_within_limit(_render, new_articles, new_trials)
				)

				if html_content is None:
					reason = (
						f"Rendered admin summary for list '{admin_list.list_name}' "
						f"still exceeds the safe body size after shrinking to a "
						f"single article and a single trial."
					)
					logger.error(reason)
					FailedNotification.objects.create(
						subscriber=subscriber, list=admin_list, reason=reason
					)
					continue

				text_content = strip_tags(html_content)

				# Step 5: Send email
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

				if result and result.status_code == 200:
					response_data = result.json()
					error_code = response_data.get("ErrorCode", 0)
					message = response_data.get("Message", "Unknown error")

					if error_code == 0:  # Successful delivery
						self.stdout.write(
							self.style.SUCCESS(
								f"Email sent to {subscriber.email} for list '{admin_list.list_name}'."
							)
						)
						# Record sent notifications only for content that was
						# actually rendered into the email (post-shrink).
						for article in articles_to_be_sent:
							SentArticleNotification.objects.get_or_create(
								article=article, list=admin_list, subscriber=subscriber
							)
						for trial in trials_to_be_sent:
							SentTrialNotification.objects.get_or_create(
								trial=trial, list=admin_list, subscriber=subscriber
							)
					else:
						self.stdout.write(
							self.style.ERROR(
								f"Failed to send email to {subscriber.email} for list '{admin_list.list_name}'. Reason: {message}"
							)
						)
						FailedNotification.objects.create(
							subscriber=subscriber, list=admin_list, reason=message
						)
				else:
					# Enhanced error handling for non-200 status codes
					error_details = (
						f"HTTP Status {result.status_code if result else 'No Response'}"
					)

					# For 422 errors, extract detailed Postmark error information
					if result and result.status_code == 422:
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
							f"Failed to send email to {subscriber.email} for list '{admin_list.list_name}'. {error_details}"
						)
					)
					FailedNotification.objects.create(
						subscriber=subscriber, list=admin_list, reason=error_details
					)
