"""
gregory/middleware/visibility.py

Attaches ``request.visible_org_ids`` (a ``set[int]``) to every incoming
request so that viewsets and serializers can read it without recomputing.

The set is computed by ``gregory.visibility.visible_org_ids`` which
implements the access-control rules from the spec (§4.1).
"""


class VisibleOrgMiddleware:
	def __init__(self, get_response):
		self.get_response = get_response

	def __call__(self, request):
		from gregory.visibility import visible_org_ids
		request.visible_org_ids = visible_org_ids(request)
		return self.get_response(request)
