from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.template.loader import get_template
from django.utils.html import strip_tags
from gregory.models import Articles, Trials, Team, Subject
from sitesettings.models import *
from subscriptions.models import Subscribers
import datetime
import requests

class Command(BaseCommand):
    help = 'Sends an admin summary every 2 days.'

    def handle(self, *args, **options):
        customsettings = CustomSetting.objects.get(site=Site.objects.get_current().id)
        site = Site.objects.get_current()
        # Get Teams
        teams = Team.objects.all()
        for team in teams:
            members = team.members
            subjects = team.subjects.all()
            for subject in subjects:
                # fetch the articles and trials that were not sent to the team it will be something like the following but we need to find a better way to track if the article was sent to that user
                articles = Articles.objects.filter(subjects=subject).exclude(sent_to_teams=team)
                trials = Trials.objects.filter(subjects=subject).exclude(sent_to_teams=team)
                results = []
                for member in members:
                    print(f"sending to {member.email}")  # Fixed from admin.email to member.email
                    summary = {
                        "articles": articles,
                        "trials": trials,
                        "admin": member.email,  # Fixed from admin.email to member.email
                        "title": customsettings.title,
                        "email_footer": customsettings.email_footer,
                        "site": site,
                    }
                    to = member.email  # Fixed from admin.email to member.email
                    html = get_template('emails/admin_summary.html').render(summary)
                    text = strip_tags(html)
                    result = self.send_simple_message(to=to, subject='Admin Summary', html=html, text=text)
                    results.append(result.status_code)
                    # carefull, this will not keep track of a single failed delivery of the email
                    if 200 in results:
                        for article in articles:
                            article.sent_to_teams.add(team)
                        for trial in trials:
                            trial.sent_to_teams.add(team)

    def send_simple_message(self, to, bcc=None, subject=None, text=None, html=None, 
                            sender=f'Gregory MS <gregory@mg.{Site.objects.get_current().domain}>',
                            email_mailgun_api_url=settings.EMAIL_MAILGUN_API_URL, 
                            email_mailgun_api=settings.EMAIL_MAILGUN_API):
        print(f"data=sender: {sender}, to: {to}, bcc: {bcc}")
        status = requests.post(
            email_mailgun_api_url,
            auth=("api", email_mailgun_api),
            data={"from": sender, "to": to, "bcc": bcc, "subject": subject, "text": text, "html": html}
        )
        return status