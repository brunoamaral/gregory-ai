from django.shortcuts import render
from admin import settings
from subscriptions.forms import SubscribersForm
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
		subscriptions = subscriber_form.cleaned_data['subscriptions']
		subscriber, created = Subscribers.objects.get_or_create( email=email, defaults={'first_name': first_name, 'last_name': last_name, 'profile':profile},)

		subscriber.subscriptions.add(subscriptions)
	
	return HttpResponseRedirect('https://gregory-ms.com/' 'patients/#success')
	