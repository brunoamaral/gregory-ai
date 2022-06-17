from django.shortcuts import render
from admin import settings
from subscriptions.forms import SubscribersForm
from subscriptions.models import Subscribers, Lists
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponseRedirect
import logging
import os
# Create your views here.

@csrf_exempt
def subscribe_view(request):
	logging.basicConfig(level=logging.INFO)
	logger = logging.getLogger('__name__')
	# Some processing if needed
	subscriber_form = SubscribersForm(request.POST)
	# check whether it's valid:
	if subscriber_form.is_valid():
		first_name = subscriber_form.cleaned_data['first_name']
		last_name = subscriber_form.cleaned_data['last_name']
		email = subscriber_form.cleaned_data['email']
		profile = subscriber_form.cleaned_data['profile']
		list = subscriber_form.cleaned_data['list']
		subscriber, created = Subscribers.objects.get_or_create( email=email, first_name=first_name, last_name=last_name)
		subscriber.profile = profile
		subscriber.subscriptions.add(list)
		subscriber.save()
		return HttpResponseRedirect('https://gregory-ms.com/patients/#success')
	else:
		logger.error("Django log...")
		logger.error(subscriber_form.is_valid())
		logger.error(subscriber_form.errors)
		logger.error(subscriber_form.cleaned_data['list'])
		logger.error(subscriber_form.cleaned_data)
		return HttpResponseRedirect('https://gregory-ms.com/patients/#fail')
