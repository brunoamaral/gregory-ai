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

Resolution is delegated to ``gregory.visibility._resolve_api_scheme``, which
only validates key existence, the begin/end date window, and (when
configured) the IP allowlist. It deliberately runs none of the quota
COUNT queries that ``api.utils.utils.checkValidAccess`` performs — this
attribute is touched by serializers on every request that carries an
Authorization header, including plain GETs, which are never written to
``APIAccessSchemeLog`` and so would otherwise be metered for nothing.
Quota enforcement stays where it belongs: in ``checkValidAccess``, called
directly by the write endpoints (``post_article``, ``edit_article``,
``edit_trial``).
"""

from django.utils.functional import SimpleLazyObject


class ApiKeyMiddleware:
	def __init__(self, get_response):
		self.get_response = get_response

	def __call__(self, request):
		from gregory.visibility import _resolve_api_scheme

		request.api_access_scheme = SimpleLazyObject(
			lambda: _resolve_api_scheme(request)
		)
		return self.get_response(request)
