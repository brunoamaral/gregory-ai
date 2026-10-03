"""
mcpauth/registration.py

Client registration is open (D20): MCP clients register themselves before any
user has signed in, either dynamically (RFC 7591) or by naming a Client ID
Metadata Document. DOT does the protocol for both; this adds what open
registration needs, applied the same way to both doors:

- redirect URIs are https, or http on a loopback host (a native client's
  ephemeral port, RFC 8252). An http redirect to anywhere else would send the
  authorization code over the network in the clear.
- authorization-code clients only (with refresh_token). A client asking for the
  password, client-credentials, implicit or device grant is told so, instead of
  DOT creating a client that the token endpoint would then refuse for a less
  obvious reason.
- dynamic registrations are limited per client address.
- an RFC 7592 update (``PUT /o/register/<client_id>/``) passes the same policy:
  DOT's own handler would let a registered client switch to any grant type or
  a plain-http redirect.
"""

import json
from urllib.parse import urlsplit

from django.conf import settings
from django.http import JsonResponse
from oauth2_provider.cimd import CIMDError, SafeMetadataFetcher
from oauth2_provider.views.dynamic_client_registration import (
	DynamicClientRegistrationManagementView,
	DynamicClientRegistrationView,
)

from mcpauth import throttle

ALLOWED_REGISTRATION_GRANTS = {"authorization_code", "refresh_token"}
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
REGISTRATION_WINDOW_SECONDS = 60 * 60


def check_registration_metadata(metadata: dict) -> None:
	"""Raise ValueError if the requested client is not one we register."""
	grants = metadata.get("grant_types", ["authorization_code"])
	if not isinstance(grants, list) or not set(grants) <= ALLOWED_REGISTRATION_GRANTS:
		raise ValueError("Only the authorization_code grant (with refresh_token) is supported.")

	uris = metadata.get("redirect_uris", [])
	if not isinstance(uris, list):
		return  # DOT reports the malformed list
	for uri in uris:
		if not isinstance(uri, str):
			continue
		parts = urlsplit(uri)
		if parts.scheme == "https":
			continue
		if parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS:
			continue
		raise ValueError("Redirect URIs must be https, or http on a loopback address.")


class PolicyMetadataFetcher(SafeMetadataFetcher):
	"""DOT's SSRF-hardened document fetcher, followed by our registration policy.

	``fetch_document`` is the network step, kept separate so tests can stand in
	for it without losing the policy.
	"""

	def fetch(self, client_id):
		metadata, max_age = self.fetch_document(client_id)
		try:
			check_registration_metadata(metadata)
		except ValueError as exc:
			raise CIMDError(str(exc)) from exc
		return metadata, max_age

	def fetch_document(self, client_id):
		return super().fetch(client_id)


def _error(error: str, description: str, status: int = 400) -> JsonResponse:
	return JsonResponse({"error": error, "error_description": description}, status=status)


def _policy_error(request):
	"""The registration-policy error for a request body, or None. A body that
	isn't a JSON object is left for DOT to report."""
	try:
		metadata = json.loads(request.body)
	except ValueError:
		return None
	if not isinstance(metadata, dict):
		return None
	try:
		check_registration_metadata(metadata)
	except ValueError as exc:
		return _error("invalid_client_metadata", str(exc))
	return None


class RegistrationView(DynamicClientRegistrationView):
	def post(self, request, *args, **kwargs):
		if (
			throttle.hit("dcr", throttle.client_address(request), REGISTRATION_WINDOW_SECONDS)
			> settings.OAUTH_DCR_MAX_PER_HOUR
		):
			response = _error("too_many_requests", "Too many registrations from this address. Try again later.", 429)
			response["Retry-After"] = str(REGISTRATION_WINDOW_SECONDS)
			return response

		return _policy_error(request) or super().post(request, *args, **kwargs)


class RegistrationManagementView(DynamicClientRegistrationManagementView):
	def put(self, request, *args, **kwargs):
		return _policy_error(request) or super().put(request, *args, **kwargs)

