from django.core.management.base import BaseCommand
from django.db.models import Q
from django.template.loader import get_template
from django.utils.html import strip_tags
from gregory.models import Trials
from sitesettings.models import CustomSetting
from subscriptions.models import Subscribers
import requests
from django.contrib.sites.models import Site
from django.conf import settings

class Command(BaseCommand):
    help = 'Sends real-time notifications for new clinical trials to subscribers.'

    def handle(self, *args, **options):
        customsettings = CustomSetting.objects.get(site=Site.objects.get_current().id)
        site = Site.objects.get_current()
        
        subscribers_email_list = Subscribers.objects.filter(
            subscriptions__list_name='Clinical Trials',
            active=True
        ).values_list('email', flat=True)

        trials = Trials.objects.filter(sent_real_time_notification=False)
        if trials and subscribers_email_list:
            summary_context = {
                "trials": trials,
                "title": customsettings.title,
                "email_footer": customsettings.email_footer,
                "site": site,
            }

            html_content = get_template('emails/trial_notification.html').render(summary_context)
            text_content = strip_tags(html_content)

            # Send an email notification to each subscriber individually
            for subscriber_email in subscribers_email_list:
                result = self.send_simple_message(
                    to=subscriber_email,
                    subject='There is a new clinical trial',
                    html=html_content,
                    text=text_content,
                    site=site,
                    customsettings=customsettings
                )

                # Check if the email was successfully sent
                if result.status_code == 200:
                    self.stdout.write(self.style.SUCCESS(f'Email sent to {subscriber_email}.'))
                else:
                    self.stdout.write(self.style.ERROR(f'Failed to send email to {subscriber_email}. Status: {result.status_code}'))

            # Mark trials as notified only after all emails are sent
            trials.update(sent_real_time_notification=True)
        else:
            self.stdout.write(self.style.WARNING('No new trials or subscribers found for notifications.'))

    def send_simple_message(self, to, subject, html, text, site, customsettings):
        sender = 'GregoryAI <gregory@' + site.domain + '>'
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

        # Send the request to Postmark
        response = requests.post(
            email_postmark_api_url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Postmark-Server-Token": email_postmark_api,
            },
            json=payload
        )

        # Output the response for debugging
        print("Status Code:", response.status_code)
        print("Response:", response.json())
        
        return response