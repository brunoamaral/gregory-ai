"""
mcpauth/urls.py

The authorization server's routes, in one namespace (``oauth2_provider``)
because DOT's own views reverse names inside it. Mounted at the root of the
API domain, since RFC 8414 puts the metadata document at
``/.well-known/oauth-authorization-server`` and the endpoints it advertises
under ``/o/``.

Only the routes an MCP editor connector needs: no device flow, no OIDC, no
application management pages. Client registration is the way in.
"""

from django.urls import path
from oauth2_provider import views as dot_views

from mcpauth import views
from mcpauth.registration import RegistrationManagementView, RegistrationView

app_name = "oauth2_provider"

urlpatterns = [
	path(
		".well-known/oauth-authorization-server",
		dot_views.OAuthServerMetadataView.as_view(),
		name="oauth-server-metadata",
	),
	path("o/authorize/", views.EditorAuthorizationView.as_view(), name="authorize"),
	path("o/token/", dot_views.TokenView.as_view(), name="token"),
	path("o/revoke/", dot_views.RevokeTokenView.as_view(), name="revoke-token"),
	path("o/introspect/", views.IntrospectView.as_view(), name="introspect"),
	path("o/register/", RegistrationView.as_view(), name="dcr-register"),
	path(
		"o/register/<str:client_id>/",
		RegistrationManagementView.as_view(),
		name="dcr-register-management",
	),
	path("o/login/", views.LoginView.as_view(), name="login"),
]
