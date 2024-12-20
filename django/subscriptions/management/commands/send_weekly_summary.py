from django.core.management.base import BaseCommand
from django.template.loader import get_template
from django.utils.html import strip_tags
from django.contrib.sites.models import Site
from django.conf import settings
from gregory.models import Articles, Trials, Subject
from sitesettings.models import CustomSetting
from subscriptions.models import (
	Lists,
	Subscribers,
	SentArticleNotification,
	SentTrialNotification
)
import requests

class Command(BaseCommand):
	help = 'Sends a weekly digest email for all weekly digest lists.'

	def handle(self, *args, **options):
		site = Site.objects.get_current()
		customsettings = CustomSetting.objects.get(site=site)

		# Step 1: Find all lists that are a weekly digest
		weekly_digest_lists = Lists.objects.filter(weekly_digest=True, subjects__isnull=False).distinct()

		if not weekly_digest_lists.exists():
			self.stdout.write(self.style.WARNING('No lists marked as weekly digest with subjects found.'))
			return

		for digest_list in weekly_digest_lists:
			# Step 2: Get the subjects for this list
			list_subjects = digest_list.subjects.all()

			if not list_subjects.exists():
				self.stdout.write(self.style.WARNING(f'The list "{digest_list.list_name}" is marked as weekly digest but has no subjects.'))
				continue

			# Step 3: Gather clinical trials and articles for the subjects of this list
			articles = Articles.objects.filter(subjects__in=list_subjects).distinct()
			trials = Trials.objects.filter(subjects__in=list_subjects).distinct()

			# Step 4: Send the digest to the subscribers of this list
			subscribers = Subscribers.objects.filter(
				active=True,
				subscriptions=digest_list
			).distinct()

			if not subscribers.exists():
				self.stdout.write(self.style.WARNING(f'No active subscribers found for the weekly digest list "{digest_list.list_name}".'))
				continue

			for subscriber in subscribers:
				# Filter out articles already sent to this subscriber for this list
				sent_article_ids = SentArticleNotification.objects.filter(
					article__in=articles,
					list=digest_list,
					subscriber=subscriber
				).values_list('article_id', flat=True)

				unsent_articles = articles.exclude(pk__in=sent_article_ids)

				# Filter out trials already sent to this subscriber for this list
				sent_trial_ids = SentTrialNotification.objects.filter(
					trial__in=trials,
					list=digest_list,
					subscriber=subscriber
				).values_list('trial_id', flat=True)

				unsent_trials = trials.exclude(pk__in=sent_trial_ids)

				# If there's nothing new, skip this subscriber
				if not unsent_articles.exists() and not unsent_trials.exists():
					self.stdout.write(
						self.style.WARNING(f'No new articles or trials for {subscriber.email} in list "{digest_list.list_name}".')
					)
					continue

				# Prepare and send the email
				summary_context = {
					"articles": unsent_articles,
					"trials": unsent_trials,
					"title": customsettings.title,
					"email_footer": customsettings.email_footer,
					"site": site,
				}

				html_content = get_template('emails/weekly_summary.html').render(summary_context)
				text_content = strip_tags(html_content)

				result = self.send_simple_message(
					to=subscriber.email,
					subject=f'Your Weekly Digest: {digest_list.list_name}',
					html=html_content,
					text=text_content,
					site=site,
					customsettings=customsettings
				)

				if result.status_code == 200:
					self.stdout.write(
						self.style.SUCCESS(f'Weekly digest email sent to {subscriber.email} for list "{digest_list.list_name}".')
					)
					# Step 5: Record that these emails were already sent
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
				else:
					self.stdout.write(
						self.style.ERROR(f'Failed to send weekly digest email to {subscriber.email} for list "{digest_list.list_name}". Status: {result.status_code}')
					)

	def send_simple_message(self, to, subject, html, text, site, customsettings):
		sender = f'GregoryAI <gregory@{site.domain}>'
		email_postmark_api_url = settings.EMAIL_POSTMARK_API_URL
		email_postmark_api = settings.EMAIL_POSTMARK_API

		payload = {
			"MessageStream": "broadcast",
			"From": sender,
			"To": to,
			"Subject": subject,
			"TextBody": text,
			"HtmlBody": html
		}

		response = requests.post(
			email_postmark_api_url,
			headers={
				"Accept": "application/json",
				"Content-Type": "application/json",
				"X-Postmark-Server-Token": email_postmark_api,
			},
			json=payload
		)

		print("Status Code:", response.status_code)
		print("Response:", response.json())

		return response