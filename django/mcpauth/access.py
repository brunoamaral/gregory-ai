"""
mcpauth/access.py

Which site an OAuth request is for, and what the signed-in person may do
there. Everything that decides a token's site and tier lives here, so the
consent screen, the token endpoint and introspection cannot disagree.

Site comes from the RFC 8707 ``resource`` the client names: the editor address
``https://<host>/mcp/editor``. The host is matched to a Site the way the MCP
server matches a tenant (exact domain, then one subdomain level stripped), so
``gregory-ai.brain-regeneration.com`` is the site ``brain-regeneration.com``.
"""

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.sites.models import Site

from gregory.site_resolution import find_site_by_domain
from mcpauth.models import (
	SCOPE_EDIT,
	SCOPE_READ,
	TIER_EDITOR,
	TIER_PUBLIC,
	SiteEditor,
)
from sitesettings.models import CustomSetting

EDITOR_PATH = "/mcp/editor"


class ResourceError(Exception):
	"""The ``resource`` is not the editor address of an MCP-enabled site."""


@dataclass(frozen=True)
class SiteAccess:
	"""What one person may do on one site."""

	site: Site
	tier: str
	can_edit: bool
	contact_email: str = ""

	@property
	def scopes(self) -> list:
		scopes = [SCOPE_READ]
		if self.tier == TIER_EDITOR and self.can_edit:
			scopes.append(SCOPE_EDIT)
		return scopes

	def clamp_scope(self, scope: str) -> str:
		"""The requested scope string, minus anything this person may not have."""
		allowed = set(self.scopes)
		return " ".join(s for s in scope.split() if s in allowed)


def canonical_resource(site_host: str) -> str:
	return f"https://{site_host}{EDITOR_PATH}"


def resolve_resource(resource: str) -> Site:
	"""The Site whose editor address ``resource`` is, or ResourceError.

	Only the exact editor path is accepted: a token for ``/mcp/editor`` is
	never a token for some other path on the host. The scheme must be https
	(http only while DEBUG, for local development), and the site must have the
	assistant switched on.
	"""
	try:
		parts = urlsplit(resource)
		port = parts.port
		hostname = parts.hostname
	except ValueError:
		raise ResourceError("The resource is not a valid URL.")
	allowed_schemes = {"https", "http"} if settings.DEBUG else {"https"}
	if parts.scheme not in allowed_schemes or not hostname:
		raise ResourceError("The resource must be an https URL.")
	if parts.username or parts.password or parts.query or parts.fragment:
		raise ResourceError("The resource must be a plain URL.")
	if port is not None and not settings.DEBUG:
		raise ResourceError("The resource must not name a port.")
	if parts.path.rstrip("/") != EDITOR_PATH:
		raise ResourceError(f"The resource must be an editor address ending in {EDITOR_PATH}.")
	site = find_site_by_domain(hostname)
	if site is None or not CustomSetting.objects.filter(site=site, mcp_enabled=True).exists():
		raise ResourceError("No assistant is available at this address.")
	return site


def single_resource(resources) -> str:
	"""The one resource a request names, or ResourceError.

	One connector is one site (D13): a request for several resources, or for
	none, is refused rather than guessed at.
	"""
	resources = [r for r in (resources or []) if r]
	if len(resources) != 1:
		raise ResourceError("Name exactly one resource: the editor address of the site.")
	return resources[0]


def site_has_public_data(site: Site) -> bool:
	return CustomSetting.objects.filter(site=site, api_public=True).exists()


def contact_email(site: Site) -> str:
	"""The site's admin address, named on the consent screen as the contact for
	editor access."""
	return (
		CustomSetting.objects.filter(site=site)
		.exclude(admin_email__isnull=True)
		.exclude(admin_email="")
		.order_by("setting_id")
		.values_list("admin_email", flat=True)
		.first()
		or ""
	)


def active_grant(user, site) -> Optional[SiteEditor]:
	return SiteEditor.objects.filter(user=user, site=site, revoked_at__isnull=True).first()


def evaluate_access(user, site: Site) -> Optional[SiteAccess]:
	"""Editor tier with an active grant; public tier without one if the site has
	public data; otherwise None (D15)."""
	email = contact_email(site)
	grant = active_grant(user, site)
	if grant is not None:
		return SiteAccess(site, TIER_EDITOR, grant.can_edit, email)
	if site_has_public_data(site):
		return SiteAccess(site, TIER_PUBLIC, False, email)
	return None


def editor_address(site: Site) -> str:
	"""The address editors add to their MCP client for this site."""
	return canonical_resource(f"{settings.MCP_EDITOR_HOST_PREFIX}.{site.domain}")
