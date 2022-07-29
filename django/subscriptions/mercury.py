from django_cron import CronJobBase, Schedule
from django.conf import settings
from django.db.models import Q
from django.template.loader import get_template
from django.utils.html import strip_tags
from gregory.models import Articles,Trials
from sitesettings.models import *
from subscriptions.models import Subscribers,Lists
import datetime 
import requests
from django.contrib.sites.models import Site
from django.conf import settings

## Get custom settings from DB
customsettings = CustomSetting.objects.get(site=settings.SITE_ID)
site = Site.objects.get(pk=settings.SITE_ID)

list_clinical_trials = []
for email in Subscribers.objects.filter(subscriptions__list_name='Clinical Trials').values():
	list_clinical_trials.append(email['email'])

list_articles = []
for email in Subscribers.objects.filter(subscriptions__list_name='Articles').values():
	list_articles.append(email['email'])



def send_simple_message( sender='Gregory MS <gregory@mg.'+ site.domain + '>', to=None,bcc=None,subject='no subject', text=None,html=None, email_mailgun_api_url=settings.EMAIL_MAILGUN_API_URL, email_mailgun_api=settings.EMAIL_MAILGUN_API):
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
		admins = Subscribers.objects.filter(is_admin=True)
		articles = Articles.objects.filter(~Q(sent_to_admin=True))
		trials = Trials.objects.filter(~Q(sent_to_admin=True))
		results = []
		for admin in admins: 
			admin=str(admin.email)
			summary = {
			"articles": articles,
			"trials":trials,
			"admin": admin,
			"title": customsettings.title,
			"email_footer": customsettings.email_footer,
			"site": site,
			}
			to = admin.email
			html = get_template('emails/admin_summary.html').render(summary)
			text= strip_tags(html)
			result = send_simple_message(to=to,subject='Admin Summary',html=html, text=text)
			results.append(result.status_code)
		if 200 in results:
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
		if datetime.datetime.today().weekday() == 1: # only run on Tuesdays
			subscribers = []
			if Subscribers.objects.filter(subscriptions__list_name='Weekly Summary').count() > 0:
				for email in Subscribers.objects.filter(subscriptions__list_name='Weekly Summary').values():
					subscribers.append(email['email'])
				articles = Articles.objects.filter(relevant=True).filter(~Q(sent_to_subscribers=True))
				trials = Trials.objects.filter(~Q(sent_to_subscribers=True))
				summary = {
				"articles": articles,
				"trials":trials,
				"title": customsettings.title,
				"email_footer": customsettings.email_footer,
				"site": site,
				}
				html = get_template('emails/weekly_summary.html').render(summary)
				text= strip_tags(html)
				result = send_simple_message(to='weekly.subscribers@'+ site.domain, bcc=subscribers,subject='Weekly Summary',html=html, text=text)
				if result.status_code == 200:
					for article in articles:
						article.sent_to_subscribers = True
					articles.bulk_update(articles,['sent_to_subscribers'])
					for trial in trials:
							trial.sent_to_subscribers = True
					trials.bulk_update(trials,['sent_to_subscribers'])
			else:
				print('Error, no subscribers found for new articles')
	pass

class TrialsNotification(CronJobBase):
	RUN_EVERY_MINS = 620 # every 12 hours
	schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
	code = 'subscriptions.trials_notification'

	def do(self):
		trials = Trials.objects.filter(~Q(sent_real_time_notification=True))
		if len(trials) > 0 and Subscribers.filter(subscriptions__list_name='Clinical Trials').count() > 0:
			subscribers = []
			for email in Subscribers.objects.filter(subscriptions__list_name='Clinical Trials').values():
				subscribers.append(email['email'])

			summary = {
			"trials":trials,
			"title": customsettings.title,
			"email_footer": customsettings.email_footer,
			"site": site,
			}
			html = get_template('emails/trial_notification.html').render(summary)
			text= strip_tags(html)
			result = send_simple_message(to='clinical.trials@'+ site.domain, bcc=subscribers,subject='There is a new clinical trial',html=html, text=text)
			if result.status_code == 200:
				for trial in trials:
						trial.sent_real_time_notification = True
				trials.bulk_update(trials,['sent_real_time_notification'])
		else:
			print('Error, no subscribers found for new clinical trials')
	pass