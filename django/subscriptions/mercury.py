from django.conf import settings
from subscriptions.models import Subscribers,Lists
from django_cron import CronJobBase, Schedule
from django.template.loader import get_template
from gregory.models import Articles,Trials
from django.db.models import Q

list_clinical_trials = []
for email in Subscribers.objects.filter(lists__list_name='Clinical Trials').values():
	list_clinical_trials.append(email['email'])

list_articles = []
for email in Subscribers.objects.filter(lists__list_name='Articles').values():
	list_articles.append(email['email'])


import requests
def send_simple_message( sender="Greg <greg@mg.gregory-ms.com>", to=None,bcc=None,subject='no subject', text=None,html=None, email_mailgun_api_url=settings.EMAIL_MAILGUN_API_URL, email_mailgun_api=settings.EMAIL_MAILGUN_API):
	return requests.post(
			email_mailgun_api_url,
			auth=("api", email_mailgun_api),
			data={"from": sender,
						"to": to,
						"bcc": bcc,
						"subject": subject,
						"text": text,
						"html": html
						}
						)




class AdminSummary(CronJobBase):
	RUN_EVERY_MINS = 2880 # every 2 days
	# RUN_EVERY_MINS = 1
	schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
	code = 'subscriptions.admin_summary'    # a unique code

	def do(self):
		summary = {
		"articles":Articles.objects.filter(~Q(sent_to_admin=True)),
		"trials":Trials.objects.filter(~Q(sent_to_subscribers=True)),
		"admin": Subscribers.objects.get(is_admin=True)
		}
		admin=str(summary['admin'].email)
		html = get_template('emails/admin_summary.html').render(summary)
		send_simple_message(to=admin,subject='Admin Summary',html=html)
		pass