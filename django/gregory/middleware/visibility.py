"""
gregory/middleware/visibility.py

Attaches ``request.visible_org_ids`` and ``request.visible_subject_ids``
(each a ``set[int]``) to every incoming request so that viewsets and
serializers can read them without recomputing.

Both sets are computed **lazily** (on first access) by
``gregory.visibility.visible_org_ids`` / ``visible_subject_ids``.  Lazy
evaluation is required because DRF authentication (JWT, Bearer token) runs
*inside* the view dispatch cycle, after all middleware has executed.  By the
time any view or serializer reads either attribute, DRF will have resolved
``request.user`` and propagated it back to the underlying Django request, so
the visibility functions see the fully-authenticated identity.

Session-authenticated requests and raw API-key requests are unaffected: the
lazy wrapper simply evaluates on first access with the same result as eager
evaluation would have produced.

``visible_subject_ids`` is part of the site-scoped API visibility project --
see ``gregory/visibility.py`` for the per-caller rules.

As of Phase 3, resolving an anonymous caller's site can consult the
``Origin``/``Referer`` headers (``gregory.site_resolution.resolve_anonymous_site``),
which means the response can vary by ``Origin`` even though it's a
client-controlled header. When ``visible_subject_ids`` does this, it flags
the request via ``request._site_resolution_varies_by_origin`` (set as a
side effect of evaluating the SimpleLazyObject inside ``get_response()``
below), and this middleware turns that into a ``Vary: Origin`` response
header once ``get_response()`` returns -- so a cache in front of this API
never serves one Origin's resolution to another.
"""

from django.utils.cache import patch_vary_headers
from django.utils.functional import SimpleLazyObject


class VisibleOrgMiddleware:
	def __init__(self, get_response):
		self.get_response = get_response

	def __call__(self, request):
		from gregory.visibility import visible_org_ids, visible_subject_ids

		request.visible_org_ids = SimpleLazyObject(lambda: visible_org_ids(request))
		request.visible_subject_ids = SimpleLazyObject(
			lambda: visible_subject_ids(request)
		)
		response = self.get_response(request)
		if getattr(request, "_site_resolution_varies_by_origin", False):
			patch_vary_headers(response, ["Origin"])
		return response
