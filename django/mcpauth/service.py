"""
mcpauth/service.py

The service credential: the one secret the MCP server holds to talk to this
API on behalf of the editors it has verified (MCP-AUTH-PLAN.md, "Calling
Django from the MCP server"). It is not an ``APIAccessScheme`` and grants
nothing by itself: it only lets the caller introspect tokens and, together
with a verified editor, call ``/editor/``.
"""

import hmac

from django.conf import settings


def service_credential_valid(request) -> bool:
	"""True when the request carries ``Authorization: Bearer <GREGORY_MCP_SERVICE_KEY>``.

	With no key configured nothing is valid, so an unconfigured deployment
	refuses every service call rather than accepting an empty secret.
	"""
	expected = settings.GREGORY_MCP_SERVICE_KEY
	if not expected:
		return False
	scheme, _, presented = request.headers.get("Authorization", "").partition(" ")
	if scheme.lower() != "bearer" or not presented.strip():
		return False
	return hmac.compare_digest(presented.strip().encode(), expected.encode())
