"""
gregory/middleware/visibility.py

Attaches ``request.visible_org_ids`` (a ``set[int]``) to every incoming
request so that viewsets and serializers can read it without recomputing.

The set is computed **lazily** (on first access) by
``gregory.visibility.visible_org_ids``.  Lazy evaluation is required because
DRF authentication (JWT, Bearer token) runs *inside* the view dispatch cycle,
after all middleware has executed.  By the time any view or serializer reads
``request.visible_org_ids``, DRF will have resolved ``request.user`` and
propagated it back to the underlying Django request, so the visibility
function sees the fully-authenticated identity.

Session-authenticated requests and raw API-key requests are unaffected: the
lazy wrapper simply evaluates on first access with the same result as eager
evaluation would have produced.
"""

from django.utils.functional import SimpleLazyObject


class VisibleOrgMiddleware:
	def __init__(self, get_response):
		self.get_response = get_response

	def __call__(self, request):
		from gregory.visibility import visible_org_ids

		request.visible_org_ids = SimpleLazyObject(lambda: visible_org_ids(request))
		return self.get_response(request)
