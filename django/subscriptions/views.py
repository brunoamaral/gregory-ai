from django.shortcuts import render
from django import settings
from django.subscriptions.forms import SubscribersForm
from django.subscriptions.models import Subscribers

# Create your views here.
def subscribe_view(request):
	# Some processing if needed
	form = SubscribersForm(request.POST)
	# check whether it's valid:
	if form.is_valid():
		first_name = form.cleaned_data['first_name']
		last_name = form.cleaned_data['last_name']
		email = form.cleaned_data['email']
		profile = form.cleaned_data['profile']
		subscriber = Subscribers.objects.create(
			first_name=first_name,
			last_name=last_name,
			email=email,
			profile=profile
		)
		subscriber = subscriber.save()
		# if subscriber == 'OK':
		# 	return HttpResponseRedirect(settings.WEBSITE_DOMAIN + 'thank-you/')
		# else:
		# 	return HttpResponseRedirect('error/')
	return HttpResponseRedirect(settings.WEBSITE_DOMAIN + 'thank-you/')