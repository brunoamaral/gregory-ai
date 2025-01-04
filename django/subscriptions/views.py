from subscriptions.forms import SubscribersForm
from subscriptions.models import Subscribers, Lists
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponseRedirect
import logging
# Create your views here.

@csrf_exempt
def subscribe_view(request):
	logging.basicConfig(level=logging.INFO)
	logger = logging.getLogger('__name__')
	
	# Process the form data
	subscriber_form = SubscribersForm(request.POST)
	
	if subscriber_form.is_valid():
		first_name = subscriber_form.cleaned_data['first_name']
		last_name = subscriber_form.cleaned_data['last_name']
		email = subscriber_form.cleaned_data['email']
		profile = subscriber_form.cleaned_data['profile']
		list_id = subscriber_form.cleaned_data['list']
		
		try:
			# Fetch the list object
			subscription_list = Lists.objects.get(pk=list_id)

			# Check if the subscriber already exists
			subscriber, created = Subscribers.objects.get_or_create(
				email=email,
				defaults={
					'first_name': first_name,
					'last_name': last_name,
					'profile': profile
				}
			)

			# If the subscriber already exists, update their details and add the list
			if not created:
				subscriber.first_name = first_name
				subscriber.last_name = last_name
				subscriber.profile = profile

			# Add the list to the subscriber's subscriptions
			subscriber.subscriptions.add(subscription_list)
			subscriber.save()

			return HttpResponseRedirect('https://gregory-ms.com/thank-you/')

		except Lists.DoesNotExist:
			logger.error(f"List with ID {list_id} does not exist.")
			return HttpResponseRedirect('https://gregory-ms.com/error/')

	else:
		# Log errors for debugging
		logger.error("Form is invalid.")
		logger.error(subscriber_form.errors)
		return HttpResponseRedirect('https://gregory-ms.com/error/')