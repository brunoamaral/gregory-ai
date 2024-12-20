from django.core.management.base import BaseCommand
from django.template.loader import get_template
from django.utils.html import strip_tags
from django.contrib.sites.models import Site
from django.conf import settings
from gregory.models import Trials, Subject
from sitesettings.models import CustomSetting
from subscriptions.models import Subscribers, SentTrialNotification
import requests

class Command(BaseCommand):
    help = 'Sends real-time notifications for new clinical trials to subscribers, filtered by subjects, without relying on a sent flag on Trials.'

    def handle(self, *args, **options):
        customsettings = CustomSetting.objects.get(site=Site.objects.get_current().id)
        site = Site.objects.get_current()

        # Get subscribers who have lists with subjects
        subscribers = Subscribers.objects.filter(
            active=True,
            subscriptions__subjects__isnull=False
        ).distinct()

        if not subscribers.exists():
            self.stdout.write(self.style.WARNING('No subscribers found with subject-based subscriptions.'))
            return

        for subscriber in subscribers:
            subscriber_lists = subscriber.subscriptions.all()

            # Gather all subjects related to this subscriber's lists
            subscriber_subjects = Subject.objects.filter(lists__in=subscriber_lists).distinct()

            # Fetch trials matching these subjects
            matching_trials = Trials.objects.filter(
                subjects__in=subscriber_subjects
            ).distinct()

            # Determine which have already been sent to these lists
            already_sent_ids = SentTrialNotification.objects.filter(
                trial__in=matching_trials,
                list__in=subscriber_lists
            ).values_list('trial_id', flat=True)

            # Filter out already sent trials
            new_trials = matching_trials.exclude(pk__in=already_sent_ids)

            if new_trials.exists():
                summary_context = {
                    "trials": new_trials,
                    "title": customsettings.title,
                    "email_footer": customsettings.email_footer,
                    "site": site,
                }

                html_content = get_template('emails/trial_notification.html').render(summary_context)
                text_content = strip_tags(html_content)

                result = self.send_simple_message(
                    to=subscriber.email,
                    subject='There are new clinical trials',
                    html=html_content,
                    text=text_content,
                    site=site,
                    customsettings=customsettings
                )

                if result.status_code == 200:
                    self.stdout.write(self.style.SUCCESS(f'Email sent to {subscriber.email}.'))

                    # Record that these trials have been sent for each of the subscriber's lists
                    for trial in new_trials:
                        for lst in subscriber_lists:
                            SentTrialNotification.objects.get_or_create(trial=trial, list=lst)
                else:
                    self.stdout.write(self.style.ERROR(f'Failed to send email to {subscriber.email}. Status: {result.status_code}'))
            else:
                self.stdout.write(self.style.WARNING(f'No new trials found for {subscriber.email}.'))

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