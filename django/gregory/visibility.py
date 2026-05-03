"""
gregory/visibility.py

Computes the set of Organisation IDs visible to a given request.

Rules (see spec §4.1):
  - Anonymous caller (no auth, no valid API key, or null-org key)
      → public orgs only; ?include_public flag is a no-op
  - Authenticated user
      → orgs they are a member of (via any team); ?include_public=true adds public orgs
  - API key bound to org X
      → {X}; ?include_public=true adds public orgs
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
	Try to resolve an APIAccessScheme from the request's Authorization header.

	Returns the scheme object on success, None on any failure (missing key,
	invalid key, expired, IP mismatch, etc.).  Never raises.
	"""
	try:
		from api.utils.utils import getAPIKey, checkValidAccess, getIPAddress
		api_key = getAPIKey(request)
		ip_addr = getIPAddress(request)
		return checkValidAccess(api_key, ip_addr)
	except Exception:
		return None


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
