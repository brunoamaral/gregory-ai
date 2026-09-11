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
site-scoped API visibility project. See its own docstring for the
per-caller rules, which mirror the shape above with sites and subjects
standing in for organisations. Both functions are kept side by side:
org-keyed endpoints (``/teams/``, ``/organizations/``, ``/stats/`` scope
validation) keep reading ``visible_org_ids`` even though content endpoints
read subjects.

As of Phase 3, the anonymous branch of ``visible_subject_ids`` can RAISE
(``gregory.site_resolution.NoSiteResolvedError``, a DRF 400) instead of
returning a set -- see that function's docstring. This is deliberate and
safe everywhere it is actually called from (every content endpoint is a
DRF view), but it means ``visible_subject_ids`` must never be called from
a non-DRF code path for an anonymous request without also handling that
exception -- see ``gregory/site_resolution.py``'s module docstring.

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
	"""Return the set of org IDs whose make_api_public flag is True.

	The only remaining reader of make_api_public via visible_org_ids() --
	see OrganizationApiSettings' docstring for why the field stays even
	though content visibility no longer reads it: /organizations/ and the
	team_id/organization scope validations in api/views.py are org-keyed by
	design, so an org-level public flag is still the right unit for them.
	"""
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

	Site-scoped API visibility: introduced in Phase 1 alongside
	``visible_org_ids`` -- see that function's docstring for the middleware
	and lazy-evaluation contract, which this follows identically. Call
	sites are converted phase by phase; as of Phase 4 both RSS feeds
	(``rss/views.py``) read this, while ``api/views.py``, the serializers
	and the admin still read ``visible_org_ids``. Both functions are live
	at once for the duration.

	Curation is the whole grant. A subject is visible when some site the
	caller can reach lists it in ``scope_subjects`` -- nothing else confers
	access and nothing else withholds it. In particular a subject with no
	team is not special-cased: it is unreachable by default because nothing
	curates it, but an administrator who does put one in a site's scope has
	deliberately published it, and it resolves like any other. Re-checking
	the owning team here would reinstate the ownership path this project
	removes.

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
	  - Anonymous (Phase 3 -- see ``gregory.site_resolution``)
	      → the ``scope_subjects`` of ONE resolved ``api_public`` site.
	        Resolved by ``resolve_anonymous_site()``: ``?site_id=`` if it
	        names an ``api_public`` site, else the ``Origin`` header, else
	        ``Referer``, else the public union across every ``api_public``
	        site -- served automatically when that union is UNAMBIGUOUS:
	        empty (no ``api_public`` site exists -- an empty scope, not an
	        error) or exactly one site (it then just IS that site's scope).
	        Two or more ``api_public`` sites and nothing named which one
	        raises ``NoSiteResolvedError`` (a DRF 400 whose body names the
	        public sites the caller could ask for instead) -- THAT is what
	        must fail closed, because serving "everything public" there
	        would silently blend more than one site's content into one
	        anonymous response. Serving a lone public site's scope by
	        default is not a new disclosure -- that data is already
	        anyone's to read -- so refusing it would only prevent unscoped
	        *convenience*. (Amended 2026-09-10: the original design failed
	        closed on ANY unnamed site, unconditionally; that broke ~478
	        tests calling the API anonymously with no site indicator, none
	        of which were newly disclosing anything -- see the spec.) MCP
	        already sends ``?site_id=`` regardless (#860), and browser
	        callers send ``Origin``, so in practice this still only ever
	        bites anonymous server-side callers hitting a deployment with
	        two or more public sites and no site indicator: curl, scripts.

	``?include_public=true`` adds the scopes of every ``api_public`` site to
	an IDENTIFIED caller's own scope, which is how a private site's frontend
	reads public content alongside its own -- the same shape the flag has in
	``visible_org_ids``, restated in subjects (spec: "adds public
	organisations" stops being true once organisations are not the unit). It
	applies to an identified caller whose own scope came out empty too (an
	API key with no site resolved, or one whose site and organisation
	disagree): those resolve to no subjects of their own, and public
	subjects are public to everyone, so the flag still means what it says.

	It does NOT apply to the anonymous branch (Phase 3 changed this from a
	true no-op to simply not applying): an anonymous caller's resolved scope
	already belongs to an ``api_public`` site by construction, so OR-ing in
	the full public union would silently discard the very resolution this
	function just performed -- the opposite of what site resolution exists
	to guarantee. A caller that wants the full public union rather than one
	site's scope has no way to ask for it from this function; that is
	intentional, since "give me everything public" is not a resolvable site.
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

	# Anonymous caller -- Phase 3 site resolution, see docstring.
	# include_public is deliberately NOT applied here (see docstring).
	from gregory.site_resolution import NoSiteResolvedError, resolve_anonymous_site

	site_id, varies_by_origin, ambiguous = resolve_anonymous_site(request)
	if varies_by_origin:
		# Consumed by VisibleOrgMiddleware after get_response() returns, to
		# set Vary: Origin -- see that module. A plain request attribute
		# (not a return value) because this function's return type is fixed
		# by every existing call site to `set[int]`.
		request._site_resolution_varies_by_origin = True
	if ambiguous:
		raise NoSiteResolvedError()
	if site_id is None:
		# No api_public site exists at all -- an unambiguous empty union,
		# not a failure. Same shape as an identified caller whose own scope
		# came out empty (see the API-key/user branches above): zero
		# subjects, no error.
		return set()
	return set(
		# api_public=True, not just site_id=site_id: CustomSetting.site is a
		# plain FK, not OneToOne, so a site can carry a second, PRIVATE
		# settings row alongside the public one that made it eligible above.
		# Filtering on site_id alone would union that private row's own
		# scope_subjects into this anonymous response -- matching
		# _public_subject_ids()'s own filter for the same reason.
		CustomSetting.objects.filter(site_id=site_id, api_public=True)
		.exclude(scope_subjects__isnull=True)
		.values_list("scope_subjects__id", flat=True)
	)


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
