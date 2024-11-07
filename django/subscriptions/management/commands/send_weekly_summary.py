from django.core.management.base import BaseCommand
from django.db.models import Q
from django.template.loader import get_template
from django.utils.html import strip_tags
from gregory.models import Articles, Trials
from sitesettings.models import CustomSetting
from subscriptions.models import Subscribers
import requests
from django.contrib.sites.models import Site
from django.conf import settings

class Command(BaseCommand):
	help = 'Sends a weekly summary email to all subscribers every Tuesday.'

	def handle(self, *args, **options):
		customsettings = CustomSetting.objects.get(site__domain=Site.objects.get_current().domain)
		site = Site.objects.get_current()


		subscribers_email_list = Subscribers.objects.filter(
			subscriptions__list_name='Weekly Summary',
			active=True
		).values_list('email', flat=True)

		if subscribers_email_list:
			articles = Articles.objects.filter(
				Q(ml_predictions__gnb=True) |
				Q(ml_predictions__lr=True) |
				Q(ml_predictions__lsvc=True) |
				Q(ml_predictions__mnb=True) |
				Q(article_subject_relevances__is_relevant=True)
			).exclude(sent_to_subscribers=True).distinct()
			
			trials = Trials.objects.exclude(sent_to_subscribers=True)

			summary_context = {
				"articles": articles,
				"trials": trials,
				"title": customsettings.title,
				"email_footer": customsettings.email_footer,
				"site": site,
			}

			html_content = get_template('emails/weekly_summary.html').render(summary_context)
			text_content = strip_tags(html_content)

			# Send an email to each subscriber individually
			for subscriber_email in subscribers_email_list:
				result = self.send_simple_message(
					to=subscriber_email,
					subject='Weekly Summary',
					html=html_content,
					text=text_content,
					site=site,
					customsettings=customsettings
				)
			
			# Mark articles and trials as sent
			articles.update(sent_to_subscribers=True)
			trials.update(sent_to_subscribers=True)
		else:
			self.stdout.write(self.style.WARNING('No subscribers found for the Weekly Summary.'))

	def send_simple_message(self, to, subject, html, text, site, customsettings):
		# Prepare sender email and Postmark API credentials
		sender = f'GregoryAI <gregory@{site.domain}>'
		email_postmark_api_url = settings.EMAIL_POSTMARK_API_URL
		email_postmark_api = settings.EMAIL_POSTMARK_API

		# Set up the payload for the Postmark API
		payload = {
			"MessageStream": "broadcast",
			"From": sender,
			"To": to,
			"Subject": subject,
			"TextBody": text,
			"HtmlBody": html
		}

		# Make the POST request to send the email
		response = requests.post(
			email_postmark_api_url,
			headers={
				"Accept": "application/json",
				"Content-Type": "application/json",
				"X-Postmark-Server-Token": email_postmark_api,
			},
			json=payload
		)

		# Output response status for debugging
		print("Status Code:", response.status_code)
		print("Response:", response.json())
		
		return response