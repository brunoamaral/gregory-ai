"""
mcpauth/editor_auth.py

How the MCP server speaks to this API on behalf of a verified editor
(MCP-AUTH-PLAN.md, "Calling Django from the MCP server"): the service
credential proves the caller is our MCP server, and two headers name the
editor and the one site the request is for. Neither is enough alone.

Django re-checks the editor's grant on every request rather than trusting the
MCP server's cache, then the request runs as that user, restricted to that
site. No DRF imports here: ``gregory.visibility`` reads ``EditorAuth`` off the
request, and importing DRF there would be a cycle.
"""

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site

from mcpauth.access import active_grant
from mcpauth.service import service_credential_valid
from sitesettings.models import CustomSetting

HEADER_USER = "X-Gregory-Editor-User"
HEADER_SITE = "X-Gregory-Editor-Site"
EDITOR_PREFIX = "/editor/"


class EditorAuthError(Exception):
	"""The request is not a valid editor request. ``status`` is the HTTP status."""

	def __init__(self, status: int, message: str):
		super().__init__(message)
		self.status = status
		self.message = message


@dataclass(frozen=True)
class EditorAuth:
	"""A verified editor, the one site the request is for, and what they may do there.
	Set as ``request.auth`` by the editor authentication classes."""

	user_id: int
	site_id: int
	can_edit: bool


def has_editor_headers(request) -> bool:
	return HEADER_USER in request.headers or HEADER_SITE in request.headers


def check_service_credential(request) -> None:
	if not service_credential_valid(request):
		raise EditorAuthError(401, "A valid service credential is required.")


def authenticate_editor(request):
	"""The ``(user, EditorAuth)`` for a request, or EditorAuthError.

	The grant is looked up here, on every request: a revocation takes effect on
	the next call, whatever the MCP server has cached.
	"""
	check_service_credential(request)
	raw_user = request.headers.get(HEADER_USER, "")
	raw_site = request.headers.get(HEADER_SITE, "")
	if not (raw_user.isdigit() and raw_site.isdigit()):
		raise EditorAuthError(401, "The editor user and site headers are required.")

	User = get_user_model()
	user = User.objects.filter(pk=int(raw_user), is_active=True).first()
	site = Site.objects.filter(pk=int(raw_site)).first()
	if user is None or site is None:
		raise EditorAuthError(401, "Unknown editor or site.")
	if not CustomSetting.objects.filter(site=site, mcp_enabled=True).exists():
		raise EditorAuthError(403, "This site does not offer an assistant.")
	grant = active_grant(user, site)
	if grant is None:
		raise EditorAuthError(403, "No editor access to this site.")
	return user, EditorAuth(user_id=user.pk, site_id=site.pk, can_edit=grant.can_edit)


def editor_auth_of(request):
	"""The EditorAuth of a (Django or DRF) request, or None."""
	auth = getattr(request, "auth", None)
	return auth if isinstance(auth, EditorAuth) else None
