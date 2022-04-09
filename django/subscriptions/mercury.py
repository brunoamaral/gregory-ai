from admin.settings import * 
from subscriptions.models import Subscribers,Lists

list_clinical_trials = []
for email in Subscribers.objects.filter(lists__list_name='Clinical Trials').values():
	list_clinical_trials.append(email['email'])


list_articles = []
for email in Subscribers.objects.filter(lists__list_name='Articles').values():
	list_articles.append(email['email'])

import requests
def send_simple_message(to,bcc,subject, text,html):
		return requests.post(
				EMAIL_MAILGUN_API_URL,
				auth=("api", EMAIL_MAILGUN),
				data={"from": "Greg <greg@mg.gregory-ms.com>",
							"to": to,
							"bcc": bcc,
							"subject": subject,
							"text": text,
							"html": html})

send_simple_message(list_articles,'list_articles','this is a test 2248')