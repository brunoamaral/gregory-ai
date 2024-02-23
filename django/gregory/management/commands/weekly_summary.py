from django.core.management.base import BaseCommand
from django.db.models import Q
from django.template.loader import get_template
from django.utils.html import strip_tags
from gregory.models import Articles, Trials
from sitesettings.models import CustomSetting
from subscriptions.models import Subscribers
import datetime
import requests
from django.contrib.sites.models import Site

class Command(BaseCommand):
    help = 'Sends a weekly summary email to all subscribers every Tuesday.'

    def handle(self, *args, **options):
        customsettings = CustomSetting.objects.get(site__domain=Site.objects.get_current().domain)
        site = Site.objects.get_current()

        if datetime.datetime.today().weekday() == 1:  # Check if today is Tuesday
            subscribers_email_list = Subscribers.objects.filter(
                subscriptions__list_name='Weekly Summary',
                active=True
            ).values_list('email', flat=True)

            if subscribers_email_list:
                articles = Articles.objects.filter(
                    Q(ml_prediction_gnb=True) | Q(relevant=True)
                ).exclude(sent_to_subscribers=True)
                
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

                for email in subscribers_email_list:
                    self.send_simple_message(
                        to=email,
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
        sender = f'Gregory MS <gregory@mg.{site.domain}>'
        email_mailgun_api_url = customsettings.email_mailgun_api_url
        email_mailgun_api = customsettings.email_mailgun_api

        response = requests.post(
            email_mailgun_api_url,
            auth=("api", email_mailgun_api),
            data={
                "from": sender,
                "to": to,
                "subject": subject,
                "text": text,
                "html": html
            }
        )
        return response
