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

            # Send an email notification
            result = self.send_simple_message(
                to='clinical.trials@' + site.domain,
                bcc=list(subscribers_email_list),
                subject='There is a new clinical trial',
                html=html_content,
                text=text_content,
                site=site,
                customsettings=customsettings
            )

            if result.status_code == 200:
                trials.update(sent_real_time_notification=True)
            else:
                self.stdout.write(self.style.ERROR('Failed to send email notifications.'))
        else:
            self.stdout.write(self.style.WARNING('No new trials or subscribers found for notifications.'))

    def send_simple_message(self, to, bcc, subject, html, text, site, customsettings):
        sender='Gregory MS <gregory@mg.' + Site.objects.get_current().domain + '>'
        email_mailgun_api_url = settings.EMAIL_MAILGUN_API_URL
        email_mailgun_api = settings.EMAIL_MAILGUN_API

        response = requests.post(
            email_mailgun_api_url,
            auth=("api", email_mailgun_api),
            data={
                "from": sender,
                "to": to,
                "bcc": bcc,
                "subject": subject,
                "text": text,
                "html": html,
            }
        )
        return response
