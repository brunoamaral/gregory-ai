"""
mcpauth/validators.py

django-oauth-toolkit's validator, narrowed to what MCP editor access needs
(MCP-AUTH-PLAN.md): authorization code and refresh grants only, every token
bound to one site through its RFC 8707 ``resource`` (D4, D13), and the
site's tier decided here and stored on the token (D15).

DOT already carries ``resource`` through the grant, the access token and the
refresh token, narrows it on refresh, and rejects values that aren't absolute
URIs. What it doesn't do is decide what a resource *means*; that is
``mcpauth.access``.
"""

from oauthlib.oauth2.rfc6749 import errors
from oauth2_provider.models import get_access_token_model
from oauth2_provider.oauth2_validators import OAuth2Validator

from mcpauth.access import (
	ResourceError,
	SiteAccess,
	active_grant,
	contact_email,
	evaluate_access,
	resolve_resource,
	single_resource,
	site_has_public_data,
)
from mcpauth.models import SCOPE_READ, TIER_EDITOR, TIER_PUBLIC

AccessToken = get_access_token_model()

ALLOWED_GRANT_TYPES = ("authorization_code", "refresh_token")


class EditorOAuth2Validator(OAuth2Validator):
	def validate_grant_type(self, client_id, grant_type, client, request, *args, **kwargs):
		# No password, client-credentials or device grants: a token without a
		# signed-in person has no site and no tier. Registration refuses to
		# create such clients; this keeps a hand-made one from using them.
		if grant_type not in ALLOWED_GRANT_TYPES:
			return False
		return super().validate_grant_type(client_id, grant_type, client, request, *args, **kwargs)

	def _check_and_set_request_resource(self, request):
		super()._check_and_set_request_resource(request)
		request.editor_access = self._access_for_request(request)

	def _access_for_request(self, request) -> SiteAccess:
		"""The site and tier this token request resolves to, or an OAuth error."""
		try:
			site = resolve_resource(single_resource(request.resource))
		except ResourceError as exc:
			raise errors.CustomOAuth2Error(error="invalid_target", description=str(exc), request=request)

		if request.grant_type == "refresh_token":
			return self._access_for_refresh(request, site)

		access = evaluate_access(request.user, site)
		if access is None:
			# Consent already refused this person; reaching here means the
			# answer changed between consent and the code being exchanged.
			raise errors.InvalidGrantError(description=self._refusal(site), request=request)
		return access

	def _access_for_refresh(self, request, site) -> SiteAccess:
		"""A refreshed token keeps the tier it was issued with.

		Signing in again, not refreshing, is how a newly granted editor gets
		the edit tools (and how a revoked one drops to the public tier). A
		refresh only ever fails: when the grant it was issued under has ended,
		or the site has stopped serving it.
		"""
		refresh = getattr(request, "refresh_token_instance", None)
		prior = getattr(refresh, "access_token", None)
		if prior is None or prior.site_id != site.pk or prior.user_id != request.user.pk:
			raise errors.InvalidGrantError(description="The refresh token is not valid for this site.", request=request)
		email = contact_email(site)
		if prior.tier == TIER_EDITOR:
			grant = active_grant(request.user, site)
			if grant is None:
				raise errors.InvalidGrantError(description="Editor access to this site has ended.", request=request)
			return SiteAccess(site, TIER_EDITOR, grant.can_edit, email)
		if prior.tier == TIER_PUBLIC and site_has_public_data(site):
			return SiteAccess(site, TIER_PUBLIC, False, email)
		raise errors.InvalidGrantError(description="This site no longer offers public access.", request=request)

	@staticmethod
	def _refusal(site) -> str:
		email = contact_email(site)
		hint = f" Contact {email} to be given editor access." if email else ""
		return f"You do not have access to {site.name}.{hint}"

	def _create_access_token(self, expires, request, token, source_refresh_token=None):
		access = request.editor_access
		# Whatever the client asked for, the token carries only what this
		# person may have here, and always the read scope the tools need.
		granted = access.clamp_scope(token["scope"]).split()
		if SCOPE_READ not in granted:
			granted.insert(0, SCOPE_READ)
		token["scope"] = " ".join(granted)

		access_token = AccessToken(
			user=request.user,
			scope=token["scope"],
			expires=expires,
			application=request.client,
			source_refresh_token=source_refresh_token,
			resource=getattr(request, "resource", []),  # RFC 8707
			site=access.site,
			tier=access.tier,
		)
		self._set_token_value(access_token, token["access_token"])
		access_token.save()
		return access_token
