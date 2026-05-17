"""
api/middleware.py

Attaches ``request.api_access_scheme`` (an ``APIAccessScheme`` instance, or
``None``) to every incoming request so that views and serializers can read
the resolved API key's organisation without repeating the auth lookup.

Resolution is **lazy** — the lookup only happens when
``request.api_access_scheme`` is first accessed.  This mirrors the pattern
used by ``gregory.middleware.visibility.VisibleOrgMiddleware`` so that DRF
authentication has already run by the time the attribute is evaluated inside a
view or serializer.

The middleware never raises — when no key is present or the key is invalid it
sets the attribute to ``None`` and lets downstream views decide how to respond.
"""

from django.utils.functional import SimpleLazyObject


def _resolve_api_access_scheme(request):
	from api.utils.utils import checkValidAccess, getAPIKey, getIPAddress
	from api.utils.exceptions import APIError

	try:
		api_key = getAPIKey(request)
		ip_addr = getIPAddress(request)
		return checkValidAccess(api_key, ip_addr)
	except APIError:
		return None


class ApiKeyMiddleware:
	def __init__(self, get_response):
		self.get_response = get_response

	def __call__(self, request):
		request.api_access_scheme = SimpleLazyObject(
			lambda: _resolve_api_access_scheme(request)
		)
		return self.get_response(request)
