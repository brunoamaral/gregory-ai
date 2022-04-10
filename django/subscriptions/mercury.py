from django.conf import settings
from subscriptions.models import Subscribers,Lists
from django_cron import CronJobBase, Schedule
from django.template.loader import get_template
from gregory.models import Articles
from django.db.models import Q

list_clinical_trials = []
for email in Subscribers.objects.filter(lists__list_name='Clinical Trials').values():
	list_clinical_trials.append(email['email'])

list_articles = []
for email in Subscribers.objects.filter(lists__list_name='Articles').values():
	list_articles.append(email['email'])

admin_summary = {
	"articles":[],
	"trials":[]
}
admin_summary['articles'] = Articles.objects.filter(~Q(sent_to_admin=True))
import requests
def send_simple_message(to=None,bcc=None,subject='Test', text=None,html=None, email_mailgun_api_url=settings.EMAIL_MAILGUN_API_URL, email_mailgun_api=settings.EMAIL_MAILGUN_API):
	return requests.post(
			email_mailgun_api_url,
			auth=("api", email_mailgun_api),
			data={"from": "Greg <greg@mg.gregory-ms.com>",
						"to": to,
						"bcc": bcc,
						"subject": subject,
						"text": text,
						"html": html
						}
						)


html = get_template('emails/admin_summary.html').render(admin_summary)

class MyCronJob(CronJobBase):
	RUN_EVERY_MINS = 1 # every minute

	schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
	code = 'subscriptions.my_cron_job'    # a unique code

	def do(self):
		send_simple_message(to='greg@gregory-ms.com',bcc=list_articles,subject='list_articles',html=html)
		pass