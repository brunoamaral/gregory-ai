from subscriptions.forms import SubscribersForm
from subscriptions.models import Subscribers, Lists, ListSubscription, SubscriberSiteProfile
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponseRedirect, HttpResponseBadRequest
from django.shortcuts import render, get_object_or_404
from django.contrib.sites.models import Site
from django.utils.timezone import now as tz_now
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


def _get_client_ip(request):
	"""Return the real client IP.

	Priority order:
	1. CF-Connecting-IP — set by Cloudflare, always the true client IP.
	2. X-Forwarded-For  — first entry when behind a trusted reverse proxy.
	3. REMOTE_ADDR      — direct connection fallback.
	"""
	cf_ip = request.META.get('HTTP_CF_CONNECTING_IP')
	if cf_ip:
		return cf_ip.strip()
	x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
	if x_forwarded_for:
		return x_forwarded_for.split(',')[0].strip()
	return request.META.get('REMOTE_ADDR')


def _resolve_site_from_request(request):
	"""Best-effort resolution of the current Site from the request host."""
	host = request.get_host().split(':')[0]
	try:
		return Site.objects.get(domain=host)
	except Site.DoesNotExist:
		return Site.objects.get_current()


@csrf_exempt
def subscribe_view(request):
	# ``request.POST`` may contain multiple ``list`` values when the user
	# checks more than one subscription option.
	list_ids = request.POST.getlist('list')
	subscription_lists = list(Lists.objects.filter(pk__in=list_ids)) if list_ids else []

	# Determine redirect base before processing the form so we can redirect
	# to the correct domain even when the form is invalid.
	redirect_base = _get_redirect_base(request, subscription_lists)

	# Validate that every requested list ID resolved to a real List.
	if list_ids:
		found_ids = {str(lst.pk) for lst in subscription_lists}
		missing_ids = [lid for lid in list_ids if lid not in found_ids]
		if missing_ids:
			logger.error(
				"subscribe_view: requested list IDs %s do not exist in the database. "
				"Check that the form is posting correct list IDs.",
				missing_ids,
			)
			return HttpResponseRedirect(f'{redirect_base}/error/')
	else:
		# No list selected at all — nothing to subscribe to.
		logger.error(
			"subscribe_view: no list IDs submitted. "
			"The form must include at least one 'list' field value.",
		)
		return HttpResponseRedirect(f'{redirect_base}/error/')

	subscriber_form = SubscribersForm(request.POST)

	if subscriber_form.is_valid():
		first_name = subscriber_form.cleaned_data['first_name']
		last_name = subscriber_form.cleaned_data['last_name']
		email = subscriber_form.cleaned_data['email']
		profile = subscriber_form.cleaned_data.get('profile', '')

		try:
			subscriber, created = Subscribers.objects.get_or_create(
				email=email,
				defaults={
					'first_name': first_name,
					'last_name': last_name,
				}
			)
			if not created:
				subscriber.first_name = first_name
				subscriber.last_name = last_name
				subscriber.save()

			# Resolve consent context
			client_ip = _get_client_ip(request)
			source_site = _resolve_site_from_request(request)

			# Add to lists via through-model, storing consent data
			for lst in subscription_lists:
				ls, ls_created = ListSubscription.objects.get_or_create(
					subscriber=subscriber,
					list=lst,
					defaults={
						'consent_ip': client_ip,
						'consent_source_site': source_site,
						'consent_method': 'web_form',
						'is_active': True,
					},
				)
				if not ls_created and not ls.is_active:
					# Re-subscribing: reactivate and refresh consent
					ls.is_active = True
					ls.unsubscribed_at = None
					ls.consent_ip = client_ip
					ls.consent_source_site = source_site
					ls.consent_method = 'web_form'
					ls.save(update_fields=['is_active', 'unsubscribed_at', 'consent_ip', 'consent_source_site', 'consent_method'])

			# Create or update the per-site profile
			if profile and source_site:
				SubscriberSiteProfile.objects.update_or_create(
					subscriber=subscriber,
					site=source_site,
					defaults={'profile': profile},
				)

			return HttpResponseRedirect(f'{redirect_base}/thank-you/')

		except Exception as e:
			logger.error(f"Subscription error: {e}")
			return HttpResponseRedirect(f'{redirect_base}/error/')

	else:
		logger.error("Form is invalid.")
		logger.error(subscriber_form.errors)
		return HttpResponseRedirect(f'{redirect_base}/error/')


# ---------------------------------------------------------------------------
# Unsubscribe views
# ---------------------------------------------------------------------------

def _unsubscribe_confirm(request, token, scope, extra_id=None):
	"""
	Shared helper rendering GET (confirmation form) and handling POST (action).

	scope: 'list' | 'site' | 'all'
	extra_id: list_id for scope='list', site_id for scope='site', None for 'all'
	"""
	subscriber = get_object_or_404(Subscribers, unsubscribe_token=token)

	if request.method == 'POST':
		if scope == 'list':
			ListSubscription.objects.filter(
				subscriber=subscriber,
				list_id=extra_id,
				is_active=True,
			).update(is_active=False, unsubscribed_at=tz_now())
		elif scope == 'site':
			ListSubscription.objects.filter(
				subscriber=subscriber,
				list__team__site_id=extra_id,
				is_active=True,
			).update(is_active=False, unsubscribed_at=tz_now())
		elif scope == 'all':
			subscriber.active = False
			subscriber.save(update_fields=['active'])
			ListSubscription.objects.filter(
				subscriber=subscriber,
				is_active=True,
			).update(is_active=False, unsubscribed_at=tz_now())

		return render(request, 'subscriptions/unsubscribe_done.html', {
			'subscriber': subscriber,
			'scope': scope,
		})

	# GET: show confirmation page
	context = {
		'subscriber': subscriber,
		'scope': scope,
		'extra_id': extra_id,
		'token': token,
	}
	if scope == 'list':
		context['list_obj'] = get_object_or_404(Lists, pk=extra_id)
	elif scope == 'site':
		context['site_obj'] = get_object_or_404(Site, pk=extra_id)
	return render(request, 'subscriptions/unsubscribe_confirm.html', context)


def unsubscribe_list(request, token, list_id):
	"""Unsubscribe from a single list."""
	return _unsubscribe_confirm(request, token, scope='list', extra_id=list_id)


def unsubscribe_site(request, token, site_id):
	"""Unsubscribe from all lists belonging to a specific site's teams."""
	return _unsubscribe_confirm(request, token, scope='site', extra_id=site_id)


def unsubscribe_all(request, token):
	"""Global opt-out — marks subscriber as inactive and deactivates all list subscriptions."""
	return _unsubscribe_confirm(request, token, scope='all')

