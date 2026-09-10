"""
gregory/visibility.py

Computes the sets of Organisation IDs and Subject IDs visible to a given
request.

``visible_org_ids`` rules (see spec §4.1):
  - Anonymous caller (no auth, no valid API key)
      → public orgs only; ?include_public flag is a no-op
  - Authenticated user
      → orgs they are a member of (via OrganizationUser membership); ?include_public=true adds public orgs
  - API key bound to org X
      → {X}; ?include_public=true adds public orgs

``visible_subject_ids`` is the site-scoped replacement introduced by the
site-scoped API visibility project. Phase 1 adds the function and computes
it correctly; nothing reads it yet. See its own docstring for the per-caller
rules, which mirror the shape above with sites and subjects standing in for
organisations. Both functions are kept side by side: org-keyed endpoints
(``/teams/``, ``/organizations/``, ``/stats/`` scope validation) keep reading
``visible_org_ids`` even once content endpoints move to subjects.

Note: ``request.visible_org_ids`` and ``request.visible_subject_ids`` are
attached by ``VisibleOrgMiddleware`` as ``SimpleLazyObject``s so that
computation is deferred until first access.  This ensures DRF authentication
(JWT, Bearer token) has already resolved ``request.user`` before visibility
is evaluated — DRF propagates the authenticated user back to
``request._request.user`` via its user-property setter, so reading
``request.user`` here always reflects the DRF identity.
"""

from __future__ import annotations


def _public_org_ids() -> set[int]:
	"""Return the set of org IDs whose make_api_public flag is True."""
	from gregory.models import OrganizationApiSettings

	return set(
		OrganizationApiSettings.objects.filter(make_api_public=True).values_list(
			"organization_id", flat=True
		)
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
	api_key = request.headers.get("Authorization", "").strip()
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
			allowed = [i.strip() for i in scheme.ip_addresses.split(",")]
			if ip_addr not in allowed:
				return None
		return scheme
	except Exception:
		# Unexpected DB or import errors should not silently grant/deny access.
		# Log and re-raise so they surface in Sentry / server logs.
		import logging

		logging.getLogger(__name__).exception("Unexpected error in _resolve_api_scheme")
		raise


def _public_subject_ids() -> set[int]:
	"""Return the union of scope_subjects across every api_public site."""
	from sitesettings.models import CustomSetting

	return set(
		CustomSetting.objects.filter(api_public=True)
		.exclude(scope_subjects__isnull=True)
		.values_list("scope_subjects__id", flat=True)
	)


def visible_subject_ids(request) -> set[int]:
	"""
	Return the set of Subject IDs the caller is permitted to see.

	Site-scoped API visibility, Phase 1: introduces this function alongside
	``visible_org_ids`` -- see that function's docstring for the middleware
	and lazy-evaluation contract, which this follows identically. No call
	site reads this yet; conversion happens phase by phase later in the
	project (see SITE-API-VISIBILITY-PLAN.md, a local planning doc).

	Rules (see spec §"Visibility resolution"):
	  - Site-bound API key
	      → that site's ``CustomSetting.scope_subjects``, whether or not the
	        site is ``api_public`` -- this is how a private site's own
	        frontend reads its own scope. Empty set until the key's
	        ``site`` is resolved (backfilled by data migration for existing
	        keys; Phase 1 does not yet enforce that every key has one).
	  - Authenticated user
	      → the union of ``scope_subjects`` across every site owned (via
	        ``OrganizationSite``) by an organisation the user belongs to
	        (via ``OrganizationUser``).
	  - Anonymous
	      → the union of ``scope_subjects`` across every ``api_public``
	        site. Full site resolution (``?site_id=``, ``Origin``,
	        ``Referer``, and the fail-closed 400 when none matches) is
	        Phase 3; until then this is the entire anonymous rule. The
	        Phase 1 equivalence test asserts this matches every subject
	        visible under today's public-organisation rule.

	``?include_public=true`` adds the scopes of every ``api_public`` site to
	an identified caller's own scope, which is how a private site's frontend
	reads public content alongside its own. It is a no-op for an anonymous
	caller, whose scope is already exactly that set -- the same shape the
	flag has in ``visible_org_ids``, restated in subjects (spec: "adds public
	organisations" stops being true once organisations are not the unit).
	It applies to an identified caller whose own scope came out empty too
	(an API key with no site resolved, or one whose site and organisation
	disagree): those resolve to no subjects of their own, and public
	subjects are public to everyone, so the flag still means what it says.
	"""
	from sitesettings.models import CustomSetting

	include_public = request.GET.get("include_public", "").lower() == "true"

	def _resolve(owned: set[int]) -> set[int]:
		return owned | _public_subject_ids() if include_public else owned

	api_scheme = _resolve_api_scheme(request)
	if api_scheme is not None:
		if api_scheme.site_id is None:
			return _resolve(set())
		# The key's site must belong to the key's organisation. Both fields
		# exist and are independently editable during Phase 1 -- `organization`
		# is not retired until Phase 4 -- so a mismatched pair would otherwise
		# hand one organisation's credential another organisation's subject
		# scope. Fail closed on a mismatch rather than trusting site_id alone.
		from gregory.models import OrganizationSite

		if not OrganizationSite.objects.filter(
			organization_id=api_scheme.organization_id, site_id=api_scheme.site_id
		).exists():
			return _resolve(set())
		return _resolve(
			set(
				CustomSetting.objects.filter(site_id=api_scheme.site_id)
				.exclude(scope_subjects__isnull=True)
				.values_list("scope_subjects__id", flat=True)
			)
		)

	if getattr(request, "user", None) is not None and request.user.is_authenticated:
		from gregory.models import OrganizationSite

		org_ids = set(
			request.user.organizations_organizationuser.values_list(
				"organization_id", flat=True
			)
		)
		if not org_ids:
			return _resolve(set())
		site_ids = set(
			OrganizationSite.objects.filter(organization_id__in=org_ids).values_list(
				"site_id", flat=True
			)
		)
		if not site_ids:
			return _resolve(set())
		return _resolve(
			set(
				CustomSetting.objects.filter(site_id__in=site_ids)
				.exclude(scope_subjects__isnull=True)
				.values_list("scope_subjects__id", flat=True)
			)
		)

	# Anonymous caller -- Phase 1 rule, see docstring. include_public is a
	# no-op here: this IS the public set.
	return _public_subject_ids()


def visible_org_ids(request) -> set[int]:
	"""
	Return the set of organisation IDs the caller is permitted to see.

	The result is computed once per call; callers that need it on multiple
	occasions should cache it on the request (the middleware does this via
	``request.visible_org_ids``).
	"""
	include_public = request.GET.get("include_public", "").lower() == "true"

	owned_ids: set[int] = set()
	is_identified = False  # True when caller has a non-anonymous identity

	# --- 1. Try API key identity ---
	api_scheme = _resolve_api_scheme(request)
	if api_scheme is not None:
		owned_ids.add(api_scheme.organization_id)
		is_identified = True

	# --- 2. Try authenticated-user identity (only if no API key found) ---
	elif getattr(request, "user", None) is not None and request.user.is_authenticated:
		user_org_ids = set(
			request.user.organizations_organizationuser.values_list(
				"organization_id", flat=True
			)
		)
		owned_ids |= user_org_ids
		is_identified = True

	# --- 3. Resolve final set ---
	if not is_identified:
		# Anonymous caller → public orgs only (flag is a no-op)
		return _public_org_ids()

	if include_public:
		return owned_ids | _public_org_ids()

	return owned_ids
