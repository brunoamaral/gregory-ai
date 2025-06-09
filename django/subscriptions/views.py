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
                # ``request.POST`` may contain multiple ``list`` values when the
                # user checks more than one subscription option. ``getlist``
                # returns all of them as a list of strings.
                list_ids = request.POST.getlist('list')
		
                try:
                        # Fetch all subscription lists selected by the user
                        subscription_lists = Lists.objects.filter(pk__in=list_ids)

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

                        # Add the lists to the subscriber's subscriptions
                        subscriber.subscriptions.add(*subscription_lists)
			subscriber.save()

			return HttpResponseRedirect('https://gregory-ms.com/thank-you/')

                except Exception as e:
                        logger.error(f"Subscription error: {e}")
                        return HttpResponseRedirect('https://gregory-ms.com/error/')

	else:
		# Log errors for debugging
		logger.error("Form is invalid.")
		logger.error(subscriber_form.errors)
		return HttpResponseRedirect('https://gregory-ms.com/error/')
