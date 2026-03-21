from subscriptions.forms import SubscribersForm
from subscriptions.models import Subscribers, Lists
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponseRedirect
from django.contrib.sites.models import Site
from urllib.parse import urlparse
import logging

logger = logging.getLogger(__name__)


def _get_redirect_base(request, subscription_lists):
	"""
	Return a validated base URL (scheme + netloc) for post-subscription redirects.

	The request Origin (or Referer) must match a domain listed in
	``allowed_domains`` on at least one of the selected lists. If no match is
	found the current Site domain is used as a safe fallback.
	"""
	origin = request.META.get('HTTP_ORIGIN') or request.META.get('HTTP_REFERER', '')
	if origin:
		parsed = urlparse(origin)
		origin_netloc = parsed.netloc  # e.g. "example.com" or "example.com:8080"
		if origin_netloc:
			for lst in subscription_lists:
				allowed = [d.strip() for d in (lst.allowed_domains or '').split(',') if d.strip()]
				if origin_netloc in allowed:
					return f"{parsed.scheme}://{origin_netloc}"
	scheme = 'https' if request.is_secure() else 'http'
	domain = Site.objects.get_current().domain
	return f"{scheme}://{domain}"


@csrf_exempt
def subscribe_view(request):
	# ``request.POST`` may contain multiple ``list`` values when the user
	# checks more than one subscription option.
	list_ids = request.POST.getlist('list')
	subscription_lists = list(Lists.objects.filter(pk__in=list_ids)) if list_ids else []

	# Determine redirect base before processing the form so we can redirect
	# to the correct domain even when the form is invalid.
	redirect_base = _get_redirect_base(request, subscription_lists)

	subscriber_form = SubscribersForm(request.POST)

	if subscriber_form.is_valid():
		first_name = subscriber_form.cleaned_data['first_name']
		last_name = subscriber_form.cleaned_data['last_name']
		email = subscriber_form.cleaned_data['email']
		profile = subscriber_form.cleaned_data['profile']

		try:
			subscriber, created = Subscribers.objects.get_or_create(
				email=email,
				defaults={
					'first_name': first_name,
					'last_name': last_name,
					'profile': profile
				}
			)
			if not created:
				subscriber.first_name = first_name
				subscriber.last_name = last_name
				subscriber.profile = profile

			subscriber.subscriptions.add(*subscription_lists)
			subscriber.save()
			return HttpResponseRedirect(f'{redirect_base}/thank-you/')

		except Exception as e:
			logger.error(f"Subscription error: {e}")
			return HttpResponseRedirect(f'{redirect_base}/error/')

	else:
		logger.error("Form is invalid.")
		logger.error(subscriber_form.errors)
		return HttpResponseRedirect(f'{redirect_base}/error/')
