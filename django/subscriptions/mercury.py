from django.conf import settings
from subscriptions.models import Subscribers,Lists
from django_cron import CronJobBase, Schedule
from django.template.loader import get_template
from gregory.models import Articles,Trials
from django.db.models import Q
from django.utils.html import strip_tags
import requests
import datetime 

list_clinical_trials = []
for email in Subscribers.objects.filter(lists__list_name='Clinical Trials').values():
	list_clinical_trials.append(email['email'])

list_articles = []
for email in Subscribers.objects.filter(lists__list_name='Articles').values():
	list_articles.append(email['email'])



def send_simple_message( sender="Greg <greg@mg.gregory-ms.com>", to=None,bcc=None,subject='no subject', text=None,html=None, email_mailgun_api_url=settings.EMAIL_MAILGUN_API_URL, email_mailgun_api=settings.EMAIL_MAILGUN_API):
	status = requests.post(
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
	return status 




class AdminSummary(CronJobBase):
	RUN_EVERY_MINS = 2880 # every 2 days
	schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
	code = 'subscriptions.admin_summary'    # a unique code

	def do(self):
		admin = Subscribers.objects.get(is_admin=True)
		articles = Articles.objects.filter(~Q(sent_to_admin=True))
		trials = Trials.objects.filter(~Q(sent_to_admin=True))
		summary = {
		"articles": articles,
		"trials":trials,
		"admin": admin
		}
		admin=str(summary['admin'].email)
		html = get_template('emails/admin_summary.html').render(summary)
		text= strip_tags(html)
		result = send_simple_message(to=admin,subject='Admin Summary',html=html, text=text)
		if result.status_code == 200:
			for article in articles:
				article.sent_to_admin = True
			articles.bulk_update(articles,['sent_to_admin'])
			for trial in trials:
					trial.sent_to_admin = True
			trials.bulk_update(trials,['sent_to_admin'])
	pass

class WeeklySummary(CronJobBase):
	# RUN_EVERY_MINS = 1440 # every day
	RUN_AT_TIMES = ['8:00']
	schedule = Schedule(run_at_times=RUN_AT_TIMES)
	code = 'subscriptions.weekly_summary'    

	def do(self):
		if datetime.datetime.today().weekday() == 2: # only run on wednesdays
			subscribers = []
			for email in Subscribers.objects.filter(lists__list_name='Weekly Summary').values():
				subscribers.append(email['email'])
			articles = Articles.objects.filter(~Q(sent_to_subscribers=True))
			trials = Trials.objects.filter(~Q(sent_to_subscribers=True))
			summary = {
			"articles": articles,
			"trials":trials
			}
			html = get_template('emails/weekly_summary.html').render(summary)
			text= strip_tags(html)
			result = send_simple_message(to="weekly.subscribers@gregory-ms.com",bcc=subscribers,subject='Weekly Summary',html=html, text=text)
			if result.status_code == 200:
				# disable while we beta-test
				print('testing')
				# for article in articles:
				# 	article.sent_to_subscribers = True
				# articles.bulk_update(articles,['sent_to_subscribers'])
				# for trial in trials:
				# 		trial.sent_to_subscribers = True
				# trials.bulk_update(trials,['sent_to_subscribers'])
	pass

class TrialsNotification(CronJobBase):
	RUN_EVERY_MINS = 10 # every 10 minutes
	schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
	code = 'subscriptions.trials_notification'

	def do(self):
		trials = Trials.objects.filter(~Q(sent_real_time_notification=True))
		if len(trials) > 0:
			subscribers = []
			for email in Subscribers.objects.filter(lists__list_name='Clinical Trials').values():
				subscribers.append(email['email'])

			summary = {
			"trials":trials
			}
			html = get_template('emails/trial_notification.html').render(summary)
			text= strip_tags(html)
			result = send_simple_message(to="clinical.trials@gregory-ms.com",bcc=subscribers,subject='There is a new clinical trial',html=html, text=text)
			if result.status_code == 200:
				for trial in trials:
						trial.sent_real_time_notification = True
				trials.bulk_update(trials,['sent_real_time_notification'])
	pass