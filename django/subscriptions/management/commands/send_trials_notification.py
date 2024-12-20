from django.core.management.base import BaseCommand
from django.template.loader import get_template
from django.utils.html import strip_tags
from django.contrib.sites.models import Site
from django.conf import settings
from gregory.models import Trials, Subject
from sitesettings.models import CustomSetting
from subscriptions.models import Lists, Subscribers, SentTrialNotification
import requests

class Command(BaseCommand):
    help = 'Sends real-time notifications for new clinical trials to subscribers, filtered by subjects, without relying on a sent flag on Trials.'

    def handle(self, *args, **options):
        customsettings = CustomSetting.objects.get(site=Site.objects.get_current().id)
        site = Site.objects.get_current()

        # Step 1: Find all lists that have subjects.
        subject_lists = Lists.objects.filter(subjects__isnull=False).distinct()

        if not subject_lists.exists():
            self.stdout.write(self.style.WARNING('No lists found with subjects.'))
            return

        for lst in subject_lists:
            # Step 2: Get the subjects for this list
            list_subjects = lst.subjects.all()

            # If no subjects for some reason (shouldn't happen due to the filter, but just in case)
            if not list_subjects.exists():
                self.stdout.write(self.style.WARNING(f'List "{lst.list_name}" has no subjects. Skipping.'))
                continue

            # Step 3: Gather trials for these subjects
            list_trials = Trials.objects.filter(subjects__in=list_subjects).distinct()

            # Step 4: Find subscribers who are subscribed to this list
            subscribers = Subscribers.objects.filter(
                active=True,
                subscriptions=lst
            ).distinct()

            if not subscribers.exists():
                self.stdout.write(self.style.WARNING(f'No subscribers found for the list "{lst.list_name}".'))
                continue

            # For each subscriber, send trials not yet sent
            for subscriber in subscribers:
                # Determine which have already been sent to this list for this subscriber
                already_sent_ids = SentTrialNotification.objects.filter(
                    trial__in=list_trials,
                    list=lst,
                    subscriber=subscriber
                ).values_list('trial_id', flat=True)

                # Filter out already sent trials
                new_trials = list_trials.exclude(pk__in=already_sent_ids)

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
                        self.stdout.write(self.style.SUCCESS(f'Email sent to {subscriber.email} for list "{lst.list_name}".'))
                        # Record these trials as sent to this subscriber for this list
                        for trial in new_trials:
                            SentTrialNotification.objects.get_or_create(trial=trial, list=lst, subscriber=subscriber)
                    else:
                        self.stdout.write(self.style.ERROR(f'Failed to send email to {subscriber.email} for list "{lst.list_name}". Status: {result.status_code}'))
                else:
                    self.stdout.write(self.style.WARNING(f'No new trials found for {subscriber.email} in list "{lst.list_name}".'))

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