"""
gregory/site_resolution.py

Site-scoped API visibility, Phase 3: resolving which Site an ANONYMOUS
caller's request is for, and the conditional fail-closed 400 that results
when that cannot be pinned down to one site. See
SITE-API-VISIBILITY-SPEC.md's "Resolving a site without being told one"
(and its 2026-09-10 amendment) for the design, and gregory/visibility.py
for how this plugs into ``visible_subject_ids()``.

Resolution order: ``?site_id=`` -> ``Origin`` -> ``Referer`` -> the public
union, IF that union is unambiguous. The union of every ``api_public``
site's scope is data anyone may already read, so serving it is not a
disclosure -- it's just a convenience. "Unambiguous" means the union is
drawn from AT MOST ONE site: zero ``api_public`` sites is simply an empty
scope (no error -- there is nothing to be ambiguous about), and exactly one
means the union just IS that site's scope. It stops being safe to serve
automatically the moment a SECOND ``api_public`` site exists, because then
"the public union" would silently blend two sites' content into one
anonymous response, which nothing before this phase could express or
prevent. So: zero or one ``api_public`` site -> serve that (possibly empty)
scope directly; two or more -> refuse (``NoSiteResolvedError``, a DRF 400
naming the sites the caller could ask for instead). Two consumers are
already unaffected either way: the MCP server sends ``?site_id=`` on every
call (#860), and browser callers send ``Origin``.

This was amended 2026-09-10 after the original "refuse, always" reading
(what the spec's resolution-order list said) turned out to contradict what
its own security-property section described ("falls through to step 4 and
sees the public union") -- and, concretely, broke ~478 tests across 51+
files that call the API anonymously with no site indicator, none of which
were disclosing anything the amended rule doesn't already allow (this repo
has exactly one ``api_public`` site today). See the spec for the full
reasoning; the short version is that failing closed on AMBIGUITY, not on
ANONYMITY, is what "no unscoped mode" actually needs to mean once content
itself is already subject-scoped (Phase 4).

The property that makes an Origin header (which the client fully
controls) safe to use for this: resolution only ever considers
``api_public`` sites, at every step, so it can only ever NARROW what an
anonymous caller sees, never widen it. A caller sending
``Origin: https://some-private-site.com`` finds no match and falls
through to the public-union step -- it cannot reach a private site's scope
by claiming to come from it, whether that step ends in one site's scope or
a 400.
"""

from __future__ import annotations

from urllib.parse import urlparse

from rest_framework.exceptions import APIException


def find_site_by_domain(hostname):
	"""
	Look up a Site by exact domain, then by stripping one subdomain level.

	e.g. 'www.example.com' -> tries exact, then tries 'example.com'.
	Accepts host strings that may include a port or IPv6 brackets; these are
	normalised safely via urlparse before the lookup. Returns the matching
	Site or None.

	Shared with subscriptions/views.py (email subscribe/unsubscribe domain
	matching) -- moved here so that site-resolution logic does not depend on
	the subscriptions app. Keep its behaviour identical if you touch it; it
	has its own tests in gregory/tests/test_site_resolution.py.
	"""
	from django.contrib.sites.models import Site

	host = urlparse(f"//{hostname}").hostname or ""
	host = host.lower()
	if not host:
		return None
	try:
		return Site.objects.get(domain=host)
	except Site.DoesNotExist:
		pass
	parts = host.split(".")
	if len(parts) >= 3:
		parent = ".".join(parts[1:])
		try:
			return Site.objects.get(domain=parent)
		except Site.DoesNotExist:
			pass
	return None


def public_sites() -> list[dict]:
	"""One row per ``api_public`` site: ``[{"site_id", "domain", "name"}, ...]``.

	Shared by ``GET /sites/`` (``api.views.PublicSitesView``) and the body of
	``NoSiteResolvedError`` below, so the discovery endpoint and the 400 it's
	quoted from can never disagree about which sites exist.

	Deduped to one row per site -- ``CustomSetting.site`` is a plain FK, not
	OneToOne, so a site can carry more than one settings row -- lowest
	``setting_id`` winning, matching the tie-break ``sitesettings`` uses
	elsewhere (see ``sitesettings.utils.author_page_base``).
	"""
	from sitesettings.models import CustomSetting

	settings_qs = (
		CustomSetting.objects.filter(api_public=True)
		.select_related("site")
		.order_by("site__domain", "setting_id")
	)
	seen = set()
	result = []
	for setting in settings_qs:
		if setting.site_id in seen:
			continue
		seen.add(setting.site_id)
		result.append(
			{
				"site_id": setting.site_id,
				"domain": setting.site.domain,
				"name": setting.site.name,
			}
		)
	return result


class NoSiteResolvedError(APIException):
	"""Raised by ``visible_subject_ids()`` when an anonymous caller names no
	site AND the public union is ambiguous -- i.e. two or more ``api_public``
	sites exist, so "serve everything public" would silently blend more than
	one site's content. NOT raised merely for anonymity: with zero or one
	``api_public`` site, the union is unambiguous (empty, or exactly that
	site's scope) and ``resolve_anonymous_site`` returns it directly instead
	of raising (see its docstring and the spec's 2026-09-10 amendment).

	DRF's default exception handler turns an ``APIException`` whose
	``.detail`` is a dict (not a plain string) into a response whose body IS
	that dict -- see ``rest_framework.views.exception_handler`` -- BUT
	``APIException.__init__`` first runs that dict through
	``_get_error_details()``, which recursively ``force_str()``s every leaf
	value (it's built for validation messages, which are always text). That
	would silently turn every ``public_sites[].site_id`` int into a JSON
	*string*, making this body disagree in type with ``GET /sites/``'s own
	response -- exactly the "one code path" guarantee this class exists to
	keep. ``raw_detail`` stashes the untouched dict so ``exception_handler``
	below can restore real ints before the response renders.

	Only ever raised from inside a DRF view's dispatch cycle: every content
	endpoint that reads ``request.visible_subject_ids`` is one (Phase 4
	converted them all), and ``dispatch()``'s try/except is what turns this
	into a clean 400 instead of an unhandled exception. RSS feeds, sitemaps
	and the admin never read ``visible_subject_ids`` -- see their own module
	docstrings -- so this cannot reach those surfaces; keep it that way
	before adding a new caller of ``visible_subject_ids``.
	"""

	status_code = 400
	default_code = "site_required"

	def __init__(self):
		self.raw_detail = {
			"error": "No site could be determined for this request.",
			"detail": "Pass ?site_id=, or call from a registered site origin.",
			"public_sites": public_sites(),
		}
		super().__init__(detail=self.raw_detail)


def exception_handler(exc, context):
	"""DRF ``EXCEPTION_HANDLER`` (see ``admin/settings.py``'s
	``REST_FRAMEWORK``). Delegates to DRF's own default handler for
	everything, then restores ``NoSiteResolvedError``'s untouched
	``raw_detail`` over the string-coerced body that handler builds -- see
	that class's docstring for why. A no-op for every other exception.
	"""
	from rest_framework.views import exception_handler as _default_handler

	response = _default_handler(exc, context)
	if response is not None and isinstance(exc, NoSiteResolvedError):
		response.data = exc.raw_detail
	return response


def resolve_anonymous_site(request):
	"""
	Resolve which ``api_public`` Site an anonymous caller's visibility
	should scope to.

	Order: ``?site_id=`` -> ``Origin`` -> ``Referer`` -> the public union,
	if it is unambiguous (amended 2026-09-10 -- see module docstring). Only
	``api_public`` sites are candidates at EVERY step -- a private site's id
	or domain never resolves here, which is the safety property that makes
	trusting a client-controlled Origin/Referer header fine (see module
	docstring): it can only narrow to a public site, never grant a private
	one's scope.

	Returns ``(site_id_or_None, response_varies_by_origin, ambiguous)``:

	- A resolved ``site_id`` with ``ambiguous=False``: scope to that site.
	- ``site_id=None`` with ``ambiguous=False``: no ``api_public`` site
	  exists at all -- not a failure, just an unambiguous empty union. The
	  caller should treat this exactly like an identified caller whose own
	  scope came out empty: zero subjects, no error (see
	  ``visible_subject_ids``).
	- ``site_id=None`` with ``ambiguous=True``: two or more ``api_public``
	  sites exist and nothing named which one -- THIS is what must be
	  refused, because serving "everything public" here would silently
	  blend more than one site's content into one anonymous response.

	``response_varies_by_origin`` is True whenever the outcome could depend
	on the Origin header's value -- i.e. whenever ``?site_id=`` did not
	resolve on its own -- regardless of whether this particular request
	happened to send one, so that the caller (``gregory.visibility``, via
	the middleware) can set ``Vary: Origin`` on the response. It is False
	when ``?site_id=`` alone settled the question (Origin never consulted),
	and also False in the "no public site exists at all" case above, since
	no Origin value could have changed that outcome.
	"""
	from sitesettings.models import CustomSetting

	def _is_public_site(site_id) -> bool:
		return CustomSetting.objects.filter(
			site_id=site_id, api_public=True
		).exists()

	# ArticleFilter/TrialFilter (api/filters.py) ALSO consume this exact
	# query parameter, independently, as a content filter. Before Phase 6
	# that filter used the stale `teams__site_id` edge and could silently
	# zero out results for a site this function resolved just fine (see
	# SITE-API-VISIBILITY-SPEC.md, "site_id is broken today"). Phase 6
	# reimplemented it on `subjects__in=<that site's scope_subjects>` --
	# the same scope this function grants for a public site_id -- so the
	# two mechanisms now agree rather than fight over one query parameter.
	# They remain two independent code paths reading the same name; keep
	# them in step if either one's notion of "that site's scope" changes.
	raw_site_id = request.GET.get("site_id", "").strip()
	if raw_site_id:
		try:
			site_id = int(raw_site_id)
		except ValueError:
			site_id = None
		if site_id is not None and _is_public_site(site_id):
			return site_id, False, False

	# From here on, a different Origin could produce a different outcome --
	# whether or not THIS request sent one -- so the response varies by it.
	for header in ("HTTP_ORIGIN", "HTTP_REFERER"):
		value = request.META.get(header)
		if not value:
			continue
		try:
			hostname = urlparse(value).hostname
		except ValueError:
			# Origin/Referer are client-controlled; a malformed bracketed
			# host (e.g. "https://[::1") makes urlparse's .hostname raise
			# rather than return None. Treat it the same as "no hostname" --
			# an unresolved header, not a 500 -- and keep going: the next
			# header, then the public-union fallback, still applies.
			continue
		if not hostname:
			continue
		site = find_site_by_domain(hostname)
		if site is not None and _is_public_site(site.id):
			return site.id, True, False

	# Nothing named a site. The public union is unambiguous -- and safe to
	# serve automatically -- whenever it is drawn from at most one site:
	# zero means the union is simply empty, one means the union just IS
	# that site's scope. Two or more would silently blend their content
	# into one anonymous response, so THAT is what refuses.
	public_site_ids = list(
		CustomSetting.objects.filter(api_public=True)
		.values_list("site_id", flat=True)
		.distinct()
	)
	if len(public_site_ids) == 0:
		return None, False, False
	if len(public_site_ids) == 1:
		return public_site_ids[0], True, False
	return None, True, True
