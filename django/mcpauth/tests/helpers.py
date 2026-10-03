"""
Shared fixtures for the mcpauth tests: organisations with sites, people with
and without grants, and a driver for the whole OAuth dance (register a client,
sign in, approve, exchange the code) so tests can start from a token.
"""

import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import Client
from organizations.models import Organization

from gregory.models import OrganizationSite
from mcpauth.models import SiteEditor
from sitesettings.models import CustomSetting

User = get_user_model()

REDIRECT_URI = "https://client.example.com/callback"
SERVICE_KEY = "test-service-key-0123456789"


# Generated per test run so no fixed credential sits in the repository, where
# a secret scanner (GitGuardian) reads a username next to a password as a leak.
_PASSWORD_PREFIX = secrets.token_urlsafe(12)
TEST_CLIENT_SECRET = secrets.token_urlsafe(24)


def password_for(username):
	"""The password ``make_user`` gives ``username``."""
	return f"{_PASSWORD_PREFIX}-{username}"


def make_org(name, slug=None):
	return Organization.objects.create(name=name, slug=slug or name.lower().replace(" ", "-"))


def make_site(org, domain, *, api_public=True, mcp_enabled=True, admin_email="", default=None):
	site = Site.objects.create(domain=domain, name=domain)
	if default is None:
		default = not OrganizationSite.objects.filter(organization=org, is_default=True).exists()
	OrganizationSite.objects.create(organization=org, site=site, is_default=default)
	CustomSetting.objects.create(
		site=site,
		title=f"{domain} settings",
		api_public=api_public,
		mcp_enabled=mcp_enabled,
		admin_email=admin_email or None,
	)
	return site


def make_user(username, org=None, **extra):
	user = User.objects.create_user(username, f"{username}@example.com", password_for(username), **extra)
	if org is not None:
		org.add_user(user)
	return user


def grant(user, site, **extra):
	return SiteEditor.objects.create(user=user, site=site, **extra)


def resource_for(site, prefix="gregory-ai"):
	return f"https://{prefix}.{site.domain}/mcp/editor"


def pkce_pair(verifier="v" * 64):
	challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
	return verifier, challenge


def register_client(client=None, **overrides):
	"""Register a public client the way an MCP client does. Returns client_id."""
	client = client or Client()
	body = {
		"client_name": "Test connector",
		"redirect_uris": [REDIRECT_URI],
		"grant_types": ["authorization_code", "refresh_token"],
		"token_endpoint_auth_method": "none",
	}
	body.update(overrides)
	response = client.post("/o/register/", data=json.dumps(body), content_type="application/json")
	assert response.status_code == 201, response.content
	return response.json()["client_id"]


def authorize_params(client_id, resource, *, scope="articles:read articles:edit", challenge=None, state="st8"):
	_, default_challenge = pkce_pair()
	params = {
		"response_type": "code",
		"client_id": client_id,
		"redirect_uri": REDIRECT_URI,
		"code_challenge": challenge or default_challenge,
		"code_challenge_method": "S256",
		"state": state,
	}
	if scope is not None:
		params["scope"] = scope
	if resource is not None:
		params["resource"] = resource
	return params


def get_token(user, site, *, scope="articles:read articles:edit", client_id=None, resource=None):
	"""Sign ``user`` in, approve the consent screen for ``site`` and exchange the
	code. Returns the token endpoint's response."""
	web = Client()
	web.force_login(user)
	client_id = client_id or register_client()
	resource = resource or resource_for(site)
	verifier, challenge = pkce_pair()
	params = authorize_params(client_id, resource, scope=scope, challenge=challenge)

	consent = web.get("/o/authorize/", params)
	assert consent.status_code == 200, consent.content
	form = {
		"client_id": client_id,
		"redirect_uri": REDIRECT_URI,
		"scope": scope,
		"response_type": "code",
		"state": params["state"],
		"code_challenge": challenge,
		"code_challenge_method": "S256",
		"resource": resource,
		"allow": "Authorize",
	}
	approved = web.post("/o/authorize/", form)
	assert approved.status_code == 302, approved.content
	query = parse_qs(urlsplit(approved["Location"]).query)
	assert "code" in query, approved["Location"]

	return Client().post(
		"/o/token/",
		{
			"grant_type": "authorization_code",
			"code": query["code"][0],
			"redirect_uri": REDIRECT_URI,
			"client_id": client_id,
			"code_verifier": verifier,
			"resource": resource,
		},
	)


def introspect(token, key=SERVICE_KEY):
	headers = {"HTTP_AUTHORIZATION": f"Bearer {key}"} if key else {}
	return Client().post("/o/introspect/", {"token": token}, **headers)
