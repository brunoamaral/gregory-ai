"""
gregory/visibility.py

Computes the set of Organisation IDs visible to a given request.

Rules (see spec §4.1):
  - Anonymous caller (no auth, no valid API key, or null-org key)
      → public orgs only; ?include_public flag is a no-op
  - Authenticated user
      → orgs they are a member of (via OrganizationUser membership); ?include_public=true adds public orgs
  - API key bound to org X
      → {X}; ?include_public=true adds public orgs

Note: ``request.visible_org_ids`` is attached by ``VisibleOrgMiddleware`` as a
``SimpleLazyObject`` so that computation is deferred until first access.  This
ensures DRF authentication (JWT, Bearer token) has already resolved
``request.user`` before visibility is evaluated — DRF propagates the
authenticated user back to ``request._request.user`` via its user-property
setter, so reading ``request.user`` here always reflects the DRF identity.
"""

from __future__ import annotations


def _public_org_ids() -> set[int]:
	"""Return the set of org IDs whose make_api_public flag is True."""
	from gregory.models import OrganizationApiSettings
	return set(
		OrganizationApiSettings.objects
		.filter(make_api_public=True)
		.values_list('organization_id', flat=True)
	)


def _resolve_api_scheme(request):
	"""
	Lightweight resolution of an APIAccessScheme from the Authorization header.

	Only validates key existence, date window, and (if configured) IP address.
	Deliberately does NOT run quota-counting queries — those remain in the
	actual API views.  This avoids doubling up on DB work for every request.

	Returns the scheme object on success, None when no key is present or the
	key is invalid/expired/IP-blocked.  Re-raises unexpected exceptions.
	"""
	# Fast-path: skip entirely when no Authorization header is present.
	# This avoids any exception overhead for ordinary anonymous/session requests.
	api_key = request.headers.get('Authorization', '').strip()
	if not api_key:
		return None

	try:
		from api.models import APIAccessScheme
		from api.utils.utils import getIPAddress
		from django.utils.timezone import now as tz_now
		ip_addr = getIPAddress(request)
		current_time = tz_now()
		scheme = APIAccessScheme.objects.filter(
			api_key=api_key,
			begin_date__lte=current_time,
			end_date__gte=current_time,
		).first()
		if scheme is None:
			return None
		# Enforce IP allowlist only when the scheme has one configured
		if scheme.ip_addresses:
			allowed = [i.strip() for i in scheme.ip_addresses.split(',')]
			if ip_addr not in allowed:
				return None
		return scheme
	except Exception:
		# Unexpected DB or import errors should not silently grant/deny access.
		# Log and re-raise so they surface in Sentry / server logs.
		import logging
		logging.getLogger(__name__).exception('Unexpected error in _resolve_api_scheme')
		raise


def visible_org_ids(request) -> set[int]:
	"""
	Return the set of organisation IDs the caller is permitted to see.

	The result is computed once per call; callers that need it on multiple
	occasions should cache it on the request (the middleware does this via
	``request.visible_org_ids``).
	"""
	include_public = request.GET.get('include_public', '').lower() == 'true'

	owned_ids: set[int] = set()
	is_identified = False  # True when caller has a non-anonymous identity

	# --- 1. Try API key identity ---
	api_scheme = _resolve_api_scheme(request)
	if api_scheme is not None:
		if api_scheme.organization_id is not None:
			owned_ids.add(api_scheme.organization_id)
			is_identified = True
		# null-org key → anonymous-equivalent; is_identified stays False

	# --- 2. Try authenticated-user identity (only if no API key found) ---
	elif getattr(request, 'user', None) is not None and request.user.is_authenticated:
		user_org_ids = set(
			request.user.organizations_organizationuser
			.values_list('organization_id', flat=True)
		)
		owned_ids |= user_org_ids
		is_identified = True

	# --- 3. Resolve final set ---
	if not is_identified:
		# Anonymous or null-org key → public orgs only (flag is a no-op)
		return _public_org_ids()

	if include_public:
		return owned_ids | _public_org_ids()

	return owned_ids
