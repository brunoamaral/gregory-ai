"""
mcpauth/views.py

The pages and endpoints that are ours on the authorization server: the login
page, the consent screen (a thin layer over DOT's authorization view that adds
the site and the tier), and token introspection for the MCP server.
"""

import calendar
import hashlib

from django.conf import settings
from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_post_parameters
from oauth2_provider.exceptions import OAuthToolkitError
from oauth2_provider.models import get_access_token_model, get_application_model
from oauth2_provider.scopes import get_scopes_backend
from oauth2_provider.views import AuthorizationView
from oauthlib.oauth2.rfc6749.errors import CustomOAuth2Error

from mcpauth import throttle
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
from mcpauth.service import service_credential_valid
from sitesettings.models import CustomSetting


class ThrottledAuthenticationForm(AuthenticationForm):
	"""Django's sign-in form, refusing once a client address or a username has
	failed too often. Passwords are guessed one request at a time here, so the
	limit is on failures, and it counts both keys so rotating usernames from one
	address, or addresses against one username, each hit a wall."""

	def clean(self):
		ip = throttle.client_address(self.request)
		username = self.data.get("username", "").strip().lower()
		window = settings.OAUTH_LOGIN_FAILURE_WINDOW_SECONDS
		limit = settings.OAUTH_LOGIN_MAX_FAILURES
		if throttle.count("login-ip", ip) >= limit or throttle.count("login-user", username) >= limit:
			raise ValidationError(
				"Too many failed sign-in attempts. Wait a few minutes and try again.",
				code="throttled",
			)
		try:
			cleaned = super().clean()
		except ValidationError:
			throttle.hit("login-ip", ip, window)
			throttle.hit("login-user", username, window)
			raise
		throttle.reset("login-user", username)
		return cleaned


@method_decorator(sensitive_post_parameters(), name="dispatch")
@method_decorator(never_cache, name="dispatch")
class LoginView(auth_views.LoginView):
	"""A minimal sign-in page for the OAuth flow. Not the admin login: the
	person arrives from an MCP client and should see a page that says so."""

	template_name = "mcpauth/login.html"
	authentication_form = ThrottledAuthenticationForm
	redirect_authenticated_user = True

	def get_default_redirect_url(self):
		return "/"


@method_decorator(never_cache, name="dispatch")
class EditorAuthorizationView(AuthorizationView):
	"""DOT's authorization view, taught what an MCP editor connector needs.

	The ``resource`` names the editor address of one site; that site and the
	signed-in person's tier on it (D15) are worked out here and shown on the
	consent screen, then decided again on the POST and again when the code is
	exchanged. A request with no usable resource, or from a person with no
	access to a site that has no public data, never reaches the approve button.
	"""

	login_url = reverse_lazy("oauth2_provider:login")

	def get_login_url(self):
		return str(self.login_url)

	def _resource_error(self, message, credentials):
		return OAuthToolkitError(
			error=CustomOAuth2Error(
				error="invalid_target",
				description=message,
				state=credentials.get("state"),
			),
			redirect_uri=credentials["redirect_uri"],
		)

	def get(self, request, *args, **kwargs):
		# Every sign-in shows the consent screen: it names the site, and a
		# client must not skip it with approval_prompt=auto.
		query = request.GET.copy()
		query["approval_prompt"] = "force"
		request.GET = query

		try:
			scopes, credentials = self.validate_authorization_request(request)
		except OAuthToolkitError as error:
			return self.error_response(error, application=None)
		application = get_application_model().objects.get(client_id=credentials["client_id"])

		try:
			site = resolve_resource(single_resource(request.GET.getlist("resource")))
		except ResourceError as exc:
			return self.error_response(self._resource_error(str(exc), credentials), application)

		self.site = site
		self.access = evaluate_access(request.user, site)
		if self.access is None:
			self.oauth2_data = {"scopes": scopes}
			context = self.get_context_data(
				application=application, scopes=scopes, scopes_descriptions=[], form=None
			)
			return self.render_to_response(context, status=403)
		return super().get(request, *args, **kwargs)

	def get_context_data(self, **kwargs):
		context = super().get_context_data(**kwargs)
		access = self.access
		all_scopes = get_scopes_backend().get_all_scopes()
		requested = " ".join(context.get("scopes") or [])
		shown = (access.clamp_scope(requested) if access else "").split() or [SCOPE_READ]
		context.update(
			{
				"site": self.site,
				"access": access,
				"is_editor": access is not None and access.tier == TIER_EDITOR,
				"can_edit": access is not None and access.can_edit and access.tier == TIER_EDITOR,
				"contact_email": access.contact_email if access else contact_email(self.site),
				"scopes_descriptions": [all_scopes[s] for s in shown if s in all_scopes],
				"signed_in_as": self.request.user.get_username(),
			}
		)
		return context

	def form_invalid(self, form):
		# Only a hand-built POST gets here: the hidden fields come from a page we
		# rendered ourselves. There is no site or tier to show, so don't re-render.
		return HttpResponse("The authorization request is incomplete.", status=400)

	def form_valid(self, form):
		application = get_application_model().objects.get(client_id=form.cleaned_data["client_id"])
		credentials = {
			"state": form.cleaned_data.get("state"),
			"redirect_uri": form.cleaned_data.get("redirect_uri"),
		}
		try:
			site = resolve_resource(single_resource(form.cleaned_data.get("resource", "").split()))
		except ResourceError as exc:
			return self.error_response(self._resource_error(str(exc), credentials), application)

		self.site = site
		self.access = evaluate_access(self.request.user, site)
		if form.cleaned_data.get("allow"):
			if self.access is None:
				error = OAuthToolkitError(
					error=CustomOAuth2Error(
						error="access_denied",
						description="You do not have access to this site.",
						state=credentials["state"],
					),
					redirect_uri=credentials["redirect_uri"],
				)
				return self.error_response(error, application)
			# The hidden scope field is the client's request; the person gets
			# only what their tier allows, whatever the form says.
			granted = self.access.clamp_scope(form.cleaned_data["scope"]).split()
			if SCOPE_READ not in granted:
				granted.insert(0, SCOPE_READ)
			form.cleaned_data["scope"] = " ".join(granted)
		return super().form_valid(form)


def _json(payload, status=200):
	response = JsonResponse(payload, status=status)
	response["Cache-Control"] = "no-store"
	return response


@method_decorator(csrf_exempt, name="dispatch")
class IntrospectView(View):
	"""RFC 7662 token introspection, for the MCP server only.

	Authenticated by the service credential rather than by a client or a
	bearer token: the caller is our own MCP server, which holds no OAuth client
	of its own. Answers whether a token is live and, if so, whom it is for, on
	which site, at which tier and with which scope. Everything is re-checked
	against the current grant, so a revocation takes effect here even if a token
	somehow outlived it.
	"""

	def post(self, request, *args, **kwargs):
		if not service_credential_valid(request):
			response = _json({"error": "invalid_client"}, status=401)
			response["WWW-Authenticate"] = "Bearer"
			return response

		raw = request.POST.get("token")
		if not raw:
			return _json({"error": "invalid_request", "error_description": "Token parameter is missing."}, 400)
		return _json(self.describe(raw))

	@staticmethod
	def describe(raw_token: str) -> dict:
		inactive = {"active": False}
		checksum = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
		token = (
			get_access_token_model()
			.objects.select_related("user", "application", "site")
			.filter(token_checksum=checksum)
			.first()
		)
		# DOT's own registration-management tokens have no site and no tier.
		if token is None or token.is_expired() or not (token.site_id and token.tier and token.user_id):
			return inactive
		if not token.user.is_active:
			return inactive
		if not CustomSetting.objects.filter(site_id=token.site_id, mcp_enabled=True).exists():
			return inactive

		if token.tier == TIER_EDITOR:
			grant = active_grant(token.user, token.site)
			if grant is None:
				return inactive
			access = SiteAccess(token.site, TIER_EDITOR, grant.can_edit)
		elif token.tier == TIER_PUBLIC and site_has_public_data(token.site):
			access = SiteAccess(token.site, TIER_PUBLIC, False)
		else:
			return inactive

		granted = access.clamp_scope(token.scope)
		return {
			"active": True,
			"scope": granted,
			"exp": int(calendar.timegm(token.expires.timetuple())),
			"client_id": token.application.client_id if token.application else None,
			"username": token.user.get_username(),
			"token_type": "Bearer",
			"aud": list(token.resource or []),
			"user_id": token.user_id,
			"site_id": token.site_id,
			"tier": token.tier,
		}
