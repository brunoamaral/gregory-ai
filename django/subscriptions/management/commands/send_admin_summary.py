from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.template.loader import get_template
from django.utils.html import strip_tags
from gregory.models import Articles, Trials
from sitesettings.models import *
from subscriptions.models import Subscribers
import datetime
import requests

class Command(BaseCommand):
    help = 'Sends an admin summary every 2 days.'

    def handle(self, *args, **options):
        customsettings = CustomSetting.objects.get(site=Site.objects.get_current().id)
        site = Site.objects.get_current()

        admins = Subscribers.objects.filter(is_admin=True)
        articles = Articles.objects.filter(~Q(sent_to_admin=True))
        trials = Trials.objects.filter(~Q(sent_to_admin=True))
        results = []
        for admin in admins:
            print(f"sending to {admin.email}")
            summary = {
                "articles": articles,
                "trials": trials,
                "admin": admin.email,
                "title": customsettings.title,
                "email_footer": customsettings.email_footer,
                "site": site,
            }
            to = admin.email
            html = get_template('emails/admin_summary.html').render(summary)
            text = strip_tags(html)
            result = self.send_simple_message(to=to, subject='Admin Summary', html=html, text=text)
            results.append(result.status_code)
        if 200 in results:
            for article in articles:
                article.sent_to_admin = True
            Articles.objects.bulk_update(articles, ['sent_to_admin'])
            for trial in trials:
                trial.sent_to_admin = True
            Trials.objects.bulk_update(trials, ['sent_to_admin'])

    def send_simple_message(self, sender='Gregory MS <gregory@mg.' + Site.objects.get_current().domain + '>', to=None,bcc=None,subject='no subject', text=None,html=None, email_mailgun_api_url=settings.EMAIL_MAILGUN_API_URL, email_mailgun_api=settings.EMAIL_MAILGUN_API):
        email_mailgun_api_url = settings.EMAIL_MAILGUN_API_URL
        email_mailgun_api = settings.EMAIL_MAILGUN_API
        print(f"data=sender: {sender}, to: {to}, bcc: {bcc}, subject: {subject}, text: {text}, html: {html}")
        status = requests.post(
            email_mailgun_api_url,
            auth=("api", email_mailgun_api),
            data={"from": sender, "to": to, "bcc": bcc, "subject": subject, "text": text, "html": html}
        )
        return status