"""Tests for the MCP Phase 2 PR A models: CustomSetting.mcp_enabled /
mcp_description, SiteMcpPrompt, SiteMcpDocument, and the shared
validate_prompt_template rules. See MCP-MULTI-TENANCY-PHASE-2-PLAN.md,
task A4/A7."""

from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from .models import (
	MCP_DESCRIPTION_MAX_CHARS,
	MCP_DOCUMENT_BODY_MAX_CHARS,
	MCP_PROMPT_TEMPLATE_MAX_CHARS,
	CustomSetting,
	SiteMcpDocument,
	SiteMcpPrompt,
	validate_prompt_template,
)


class ValidatePromptTemplateTests(TestCase):
	"""Each A4 rule, exercised directly against the shared function."""

	def test_undeclared_placeholder_is_rejected(self):
		with self.assertRaises(ValidationError) as ctx:
			validate_prompt_template("Hello $name", [])
		self.assertIn("template", ctx.exception.message_dict)

	def test_unused_argument_is_rejected(self):
		with self.assertRaises(ValidationError) as ctx:
			validate_prompt_template("Hello there", [{"name": "topic"}])
		self.assertIn("arguments", ctx.exception.message_dict)

	def test_invalid_dollar_usage_is_rejected(self):
		with self.assertRaises(ValidationError) as ctx:
			validate_prompt_template("Costs $5 today", [])
		self.assertIn("template", ctx.exception.message_dict)
		self.assertIn("$$", ctx.exception.message_dict["template"][0])

	def test_unknown_argument_key_is_rejected(self):
		with self.assertRaises(ValidationError) as ctx:
			validate_prompt_template("Hello $topic", [{"name": "topic", "requried": True}])
		messages = ctx.exception.message_dict.get("arguments", [])
		self.assertTrue(any("Unknown key" in m for m in messages))

	def test_bad_argument_name_is_rejected(self):
		with self.assertRaises(ValidationError) as ctx:
			validate_prompt_template("Hello $topic", [{"name": "1topic"}])
		messages = ctx.exception.message_dict.get("arguments", [])
		self.assertTrue(any("must match" in m for m in messages))

	def test_duplicate_argument_name_is_rejected(self):
		with self.assertRaises(ValidationError) as ctx:
			validate_prompt_template(
				"Hello $topic",
				[{"name": "topic"}, {"name": "topic"}],
			)
		messages = ctx.exception.message_dict.get("arguments", [])
		self.assertTrue(any("more than once" in m for m in messages))

	def test_non_boolean_required_is_rejected(self):
		with self.assertRaises(ValidationError) as ctx:
			validate_prompt_template("Hello $topic", [{"name": "topic", "required": "yes"}])
		messages = ctx.exception.message_dict.get("arguments", [])
		self.assertTrue(any("must be true or false" in m for m in messages))

	def test_double_dollar_and_braced_placeholder_are_accepted(self):
		canonical = validate_prompt_template(
			"Costs $$5 for ${topic}", [{"name": "topic"}]
		)
		self.assertEqual(
			canonical, [{"name": "topic", "description": "", "required": True}]
		)

	def test_defaults_are_filled_in(self):
		canonical = validate_prompt_template("Hello $topic", [{"name": "topic"}])
		self.assertEqual(
			canonical, [{"name": "topic", "description": "", "required": True}]
		)


class SiteMcpPromptModelTests(TestCase):
	def setUp(self):
		self.site = Site.objects.create(domain="prompt-model.test", name="Prompt Model")

	def _make(self, **kwargs):
		defaults = {
			"site": self.site,
			"name": "a-prompt",
			"title": "A Prompt",
			"template": "Hello $topic",
			"arguments": [{"name": "topic"}],
		}
		defaults.update(kwargs)
		return SiteMcpPrompt(**defaults)

	def test_clean_normalises_arguments(self):
		prompt = self._make()
		prompt.full_clean()
		self.assertEqual(
			prompt.arguments,
			[{"name": "topic", "description": "", "required": True}],
		)

	def test_template_length_cap(self):
		prompt = self._make(
			template="$topic " + ("x" * MCP_PROMPT_TEMPLATE_MAX_CHARS),
			arguments=[{"name": "topic"}],
		)
		with self.assertRaises(ValidationError):
			prompt.full_clean()

	def test_same_name_allowed_on_two_sites(self):
		other_site = Site.objects.create(domain="prompt-model-2.test", name="Other")
		p1 = self._make()
		p1.full_clean()
		p1.save()
		p2 = self._make(site=other_site)
		p2.full_clean()
		p2.save()
		self.assertEqual(SiteMcpPrompt.objects.count(), 2)

	def test_same_name_twice_on_one_site_is_rejected(self):
		p1 = self._make()
		p1.full_clean()
		p1.save()
		p2 = self._make()
		with self.assertRaises(ValidationError):
			p2.full_clean()


class SiteMcpDocumentModelTests(TestCase):
	def setUp(self):
		self.site = Site.objects.create(domain="doc-model.test", name="Doc Model")

	def _make(self, **kwargs):
		defaults = {
			"site": self.site,
			"slug": "glossary",
			"title": "Glossary",
			"body": "Some text.",
		}
		defaults.update(kwargs)
		return SiteMcpDocument(**defaults)

	def test_reserved_slug_is_rejected(self):
		doc = self._make(slug="subjects")
		with self.assertRaises(ValidationError) as ctx:
			doc.full_clean()
		self.assertIn("slug", ctx.exception.message_dict)

	def test_body_length_cap(self):
		doc = self._make(body="x" * (MCP_DOCUMENT_BODY_MAX_CHARS + 1))
		with self.assertRaises(ValidationError):
			doc.full_clean()

	def test_same_slug_allowed_on_two_sites(self):
		other_site = Site.objects.create(domain="doc-model-2.test", name="Other")
		d1 = self._make()
		d1.full_clean()
		d1.save()
		d2 = self._make(site=other_site)
		d2.full_clean()
		d2.save()
		self.assertEqual(SiteMcpDocument.objects.count(), 2)

	def test_same_slug_twice_on_one_site_is_rejected(self):
		d1 = self._make()
		d1.full_clean()
		d1.save()
		d2 = self._make()
		with self.assertRaises(ValidationError):
			d2.full_clean()


class CustomSettingMcpFieldsTests(TestCase):
	def test_mcp_enabled_defaults_to_false(self):
		site = Site.objects.create(domain="mcp-default.test", name="Default")
		cs = CustomSetting.objects.create(site=site, title="Default MCP Site")
		self.assertFalse(cs.mcp_enabled)

	def test_mcp_description_length_cap(self):
		site = Site.objects.create(domain="mcp-desc.test", name="Desc")
		cs = CustomSetting(
			site=site,
			title="Desc Site",
			mcp_description="x" * (MCP_DESCRIPTION_MAX_CHARS + 1),
		)
		with self.assertRaises(ValidationError):
			cs.full_clean()


class SiteAdminMcpInlinesTests(TestCase):
	"""The Site admin change page, as a superuser: see task A7."""

	def setUp(self):
		self.site = Site.objects.create(domain="admin-mcp.test", name="Admin MCP")
		self.user = User.objects.create_superuser(
			username="admin", email="admin@example.test", password="pw"
		)
		self.client = Client()
		self.client.force_login(self.user)
		self.url = reverse("admin:sites_site_change", args=[self.site.pk])

	def _base_post_data(self):
		return {
			"domain": self.site.domain,
			"name": self.site.name,
			"customsetting_set-TOTAL_FORMS": "0",
			"customsetting_set-INITIAL_FORMS": "0",
			"customsetting_set-MIN_NUM_FORMS": "0",
			"customsetting_set-MAX_NUM_FORMS": "1",
			"sitemcpprompt_set-TOTAL_FORMS": "0",
			"sitemcpprompt_set-INITIAL_FORMS": "0",
			"sitemcpprompt_set-MIN_NUM_FORMS": "0",
			"sitemcpprompt_set-MAX_NUM_FORMS": "1000",
			"sitemcpdocument_set-TOTAL_FORMS": "0",
			"sitemcpdocument_set-INITIAL_FORMS": "0",
			"sitemcpdocument_set-MIN_NUM_FORMS": "0",
			"sitemcpdocument_set-MAX_NUM_FORMS": "1000",
			"organizationsite_set-TOTAL_FORMS": "0",
			"organizationsite_set-INITIAL_FORMS": "0",
			"organizationsite_set-MIN_NUM_FORMS": "0",
			"organizationsite_set-MAX_NUM_FORMS": "1000",
		}

	def test_change_page_returns_200_and_shows_both_inlines(self):
		response = self.client.get(self.url)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "MCP prompt")
		self.assertContains(response, "MCP document")

	def test_undeclared_placeholder_shows_form_error_not_500(self):
		data = self._base_post_data()
		data["sitemcpprompt_set-TOTAL_FORMS"] = "1"
		data.update(
			{
				"sitemcpprompt_set-0-site": self.site.pk,
				"sitemcpprompt_set-0-name": "broken",
				"sitemcpprompt_set-0-title": "Broken",
				"sitemcpprompt_set-0-description": "",
				"sitemcpprompt_set-0-template": "Hello $name",
				"sitemcpprompt_set-0-arguments": "[]",
				"sitemcpprompt_set-0-is_active": "on",
				"sitemcpprompt_set-0-ordering": "0",
			}
		)
		response = self.client.post(self.url, data)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(SiteMcpPrompt.objects.count(), 0)

	def test_duplicate_prompt_names_in_one_submission_show_form_error(self):
		data = self._base_post_data()
		data["sitemcpprompt_set-TOTAL_FORMS"] = "2"
		for i in range(2):
			data.update(
				{
					f"sitemcpprompt_set-{i}-site": self.site.pk,
					f"sitemcpprompt_set-{i}-name": "dupe",
					f"sitemcpprompt_set-{i}-title": f"Dupe {i}",
					f"sitemcpprompt_set-{i}-description": "",
					f"sitemcpprompt_set-{i}-template": "Hello",
					f"sitemcpprompt_set-{i}-arguments": "[]",
					f"sitemcpprompt_set-{i}-is_active": "on",
					f"sitemcpprompt_set-{i}-ordering": "0",
				}
			)
		response = self.client.post(self.url, data)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(SiteMcpPrompt.objects.count(), 0)
