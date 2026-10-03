"""
mcpauth/middleware.py

Keeps the editor headers from meaning anything to anyone but the MCP server.
``X-Gregory-Editor-User`` and ``X-Gregory-Editor-Site`` pick the identity a
request runs as, so a request that carries either must also carry the service
credential, and only ``/editor/`` routes may take them. Anything else is
refused before a view runs, whatever the route, so a client can't use them to
probe or to confuse an endpoint that never expected them.
"""

from django.http import JsonResponse

from mcpauth.editor_auth import EDITOR_PREFIX, has_editor_headers
from mcpauth.service import service_credential_valid


class EditorHeaderGuardMiddleware:
	def __init__(self, get_response):
		self.get_response = get_response

	def __call__(self, request):
		if has_editor_headers(request):
			if not service_credential_valid(request):
				return self._refuse("A valid service credential is required.")
			if not request.path.startswith(EDITOR_PREFIX):
				return self._refuse("Editor headers are only accepted on /editor/ routes.")
		return self.get_response(request)

	@staticmethod
	def _refuse(message: str) -> JsonResponse:
		response = JsonResponse({"detail": message}, status=401)
		response["WWW-Authenticate"] = "Bearer"
		return response
