from django.test import SimpleTestCase, TestCase, override_settings

from mcpauth.access import (
	ResourceError,
	editor_address,
	evaluate_access,
	resolve_resource,
	single_resource,
)
from mcpauth.tests.helpers import grant, make_org, make_site, make_user


class ResolveResourceTest(TestCase):
	def setUp(self):
		self.org = make_org("Access Org")
		self.site = make_site(self.org, "access.example.com")

	def test_host_with_the_tenant_prefix_is_the_parent_domain_site(self):
		site = resolve_resource("https://gregory-ai.access.example.com/mcp/editor")
		self.assertEqual(site, self.site)

	def test_exact_domain_matches_too(self):
		self.assertEqual(resolve_resource("https://access.example.com/mcp/editor"), self.site)

	def test_trailing_slash_is_tolerated(self):
		self.assertEqual(resolve_resource("https://gregory-ai.access.example.com/mcp/editor/"), self.site)

	def test_host_case_is_ignored(self):
		self.assertEqual(resolve_resource("https://Gregory-AI.Access.Example.com/mcp/editor"), self.site)

	def test_refused_resources(self):
		for resource in (
			"",
			"not a url",
			"https://gregory-ai.access.example.com/mcp",
			"https://gregory-ai.access.example.com/mcp/editor/x",
			"https://gregory-ai.access.example.com/mcp/editor?x=1",
			"https://gregory-ai.access.example.com/mcp/editor#frag",
			"https://user:pw@gregory-ai.access.example.com/mcp/editor",
			"https://gregory-ai.access.example.com:8443/mcp/editor",
			"http://gregory-ai.access.example.com/mcp/editor",
			"ftp://gregory-ai.access.example.com/mcp/editor",
			"https://gregory-ai.other.example.org/mcp/editor",
			"https://a.b.access.example.com/mcp/editor",
		):
			with self.subTest(resource=resource), self.assertRaises(ResourceError):
				resolve_resource(resource)

	def test_site_without_the_assistant_is_refused(self):
		make_site(self.org, "off.example.com", mcp_enabled=False)
		with self.assertRaises(ResourceError):
			resolve_resource("https://gregory-ai.off.example.com/mcp/editor")

	@override_settings(DEBUG=True)
	def test_plain_http_and_a_port_are_allowed_while_developing(self):
		self.assertEqual(resolve_resource("http://access.example.com:8001/mcp/editor"), self.site)


class SingleResourceTest(SimpleTestCase):
	def test_exactly_one_is_required(self):
		self.assertEqual(single_resource(["https://x/mcp/editor"]), "https://x/mcp/editor")
		for resources in ([], None, [""], ["a", "b"]):
			with self.subTest(resources=resources), self.assertRaises(ResourceError):
				single_resource(resources)


class EvaluateAccessTest(TestCase):
	def setUp(self):
		self.org = make_org("Eval Org")
		self.other_org = make_org("Eval Other")
		self.public = make_site(self.org, "eval-public.example.com", admin_email="a@eval.example.com")
		self.private = make_site(self.org, "eval-private.example.com", api_public=False)

	def test_active_grant_is_the_editor_tier(self):
		user = make_user("granted", self.org)
		grant(user, self.public)

		access = evaluate_access(user, self.public)

		self.assertEqual(access.tier, "editor")
		self.assertEqual(access.scopes, ["articles:read", "articles:edit"])

	def test_read_only_grant_has_no_edit_scope(self):
		user = make_user("reader", self.org)
		grant(user, self.public, can_edit=False)

		access = evaluate_access(user, self.public)

		self.assertEqual(access.tier, "editor")
		self.assertEqual(access.scopes, ["articles:read"])

	def test_no_grant_on_a_public_site_is_the_public_tier(self):
		user = make_user("plain", self.org)

		access = evaluate_access(user, self.public)

		self.assertEqual(access.tier, "public")
		self.assertEqual(access.scopes, ["articles:read"])
		self.assertEqual(access.contact_email, "a@eval.example.com")

	def test_no_grant_on_a_private_site_is_refused(self):
		self.assertIsNone(evaluate_access(make_user("plain2", self.org), self.private))

	def test_a_revoked_grant_does_not_count(self):
		user = make_user("revoked", self.org)
		grant(user, self.private).revoke()

		self.assertIsNone(evaluate_access(user, self.private))

	def test_a_grant_on_another_site_does_not_count(self):
		user = make_user("elsewhere", self.org)
		grant(user, self.public)

		self.assertIsNone(evaluate_access(user, self.private))

	def test_superuser_status_alone_gives_no_editor_access(self):
		root = make_user("root", is_superuser=True, is_staff=True)

		access = evaluate_access(root, self.public)

		self.assertEqual(access.tier, "public")
		self.assertIsNone(evaluate_access(root, self.private))

	def test_clamp_scope_drops_what_the_tier_does_not_allow(self):
		public = evaluate_access(make_user("pub", self.org), self.public)
		self.assertEqual(public.clamp_scope("articles:read articles:edit bogus"), "articles:read")


class EditorAddressTest(TestCase):
	def test_address_uses_the_tenant_prefix_and_the_site_domain(self):
		site = make_site(make_org("Addr Org"), "addr.example.com")
		self.assertEqual(editor_address(site), "https://gregory-ai.addr.example.com/mcp/editor")

	@override_settings(MCP_EDITOR_HOST_PREFIX="assistant")
	def test_prefix_is_configurable(self):
		site = make_site(make_org("Addr Org 2"), "addr2.example.com")
		self.assertEqual(editor_address(site), "https://assistant.addr2.example.com/mcp/editor")
