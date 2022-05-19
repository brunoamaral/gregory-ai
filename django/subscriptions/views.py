from django.shortcuts import render
from admin import settings
from subscriptions.forms import SubscribersForm, ListsForm
from subscriptions.models import Subscribers, Lists
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponseRedirect


# Create your views here.

@csrf_exempt
def subscribe_view(request):
	# Some processing if needed
	subscriber_form = SubscribersForm(request.POST)
	# check whether it's valid:
	if subscriber_form.is_valid():
		first_name = subscriber_form.cleaned_data['first_name']
		last_name = subscriber_form.cleaned_data['last_name']
		email = subscriber_form.cleaned_data['email']
		profile = subscriber_form.cleaned_data['profile']
		subscriber = Subscribers.objects.create(
			first_name=first_name,
			last_name=last_name,
			email=email,
			profile=profile,
		)
		# if subscriber exists:
		# 	update list 
		# else:
		# create
		subscriber = subscriber.save()
		subscriber = Subscribers.objects.get(email=email)
		listForm = ListsForm(request.POST)
		if listForm.is_valid():
			list_id = listForm.cleaned_data['list_id']
			list = Lists.objects.get(int(list_id))
			subscriber.lists_set.add(list)
		# if subscriber == 'OK':
		# 	return HttpResponseRedirect(settings.WEBSITE_DOMAIN + 'thank-you/')
		# else:
		# 	return HttpResponseRedirect('error/')
	# return HttpResponseRedirect(settings.WEBSITE_DOMAIN + 'patients/#success')
	return HttpResponseRedirect('http://localhost:1313/' 'patients/#success')
	