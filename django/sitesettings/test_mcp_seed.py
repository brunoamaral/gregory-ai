"""Tests for the MCP Phase 2 PR B seed: mcp_defaults.DEFAULT_MCP_PROMPTS,
migration 0022's seed() function, and the seed_mcp_prompts command. See
MCP-MULTI-TENANCY-PHASE-2-PLAN.md, task B5.

pytest runs with --nomigrations (pytest.ini), so migration 0022 is never
actually applied by the test runner -- its seed() function is exercised
directly, the same pattern sitesettings/tests.py uses for migration 0019.
"""

import importlib
import string

from django.contrib.sites.models import Site
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from gregory.models import Subject, Team
from organizations.models import Organization

from .mcp_defaults import DEFAULT_MCP_PROMPTS
from .models import CustomSetting, SiteMcpPrompt, validate_prompt_template

_BANNED_WORDS = ("gregory", "instance", "tenant")


class DefaultMcpPromptsAreValidTests(TestCase):
	def test_each_default_is_already_canonical(self):
		for prompt in DEFAULT_MCP_PROMPTS:
			canonical = validate_prompt_template(prompt["template"], prompt["arguments"])
			self.assertEqual(canonical, prompt["arguments"], prompt["name"])

	def test_each_default_passes_full_clean(self):
		site = Site.objects.create(domain="mcp-defaults-clean.test", name="Defaults")
		for prompt in DEFAULT_MCP_PROMPTS:
			row = SiteMcpPrompt(
				site=site,
				name=prompt["name"],
				title=prompt["title"],
				description=prompt["description"],
				template=prompt["template"],
				arguments=prompt["arguments"],
				ordering=prompt["ordering"],
			)
			row.full_clean()

	def test_substitution_leaves_no_dollar_behind(self):
		for prompt in DEFAULT_MCP_PROMPTS:
			sample = {arg["name"]: "sample-value" for arg in prompt["arguments"]}
			rendered = string.Template(prompt["template"]).substitute(sample)
			self.assertNotIn("$", rendered, prompt["name"])

	def test_no_banned_platform_words(self):
		"""Decision F: the MCP surface is single-tenant from the outside, so a
		built-in prompt must not name the platform or use "instance"/"tenant"
		vocabulary."""
		for prompt in DEFAULT_MCP_PROMPTS:
			haystack = " ".join(
				[prompt["template"], prompt["description"], prompt["title"]]
			).lower()
			for word in _BANNED_WORDS:
				self.assertNotIn(word, haystack, f"{prompt['name']}: {word!r}")


class SeedDefaultMcpPromptsMigrationTests(TestCase):
	def _run_seed(self):
		mod = importlib.import_module(
			"sitesettings.migrations.0022_seed_default_mcp_prompts"
		)
		from django.apps import apps as django_apps

		mod.seed(django_apps, schema_editor=None)

	def setUp(self):
		self.org = Organization.objects.create(name="Seed Org", slug="seed-org")
		self.team = Team.objects.create(organization=self.org, name="Seed Team", slug="seed-team")
		self.subject = Subject.objects.create(
			subject_name="Seed Subject", subject_slug="seed-subject", team=self.team
		)

	def _make_setting(self, *, title, api_public, with_scope=True):
		site = Site.objects.create(domain=f"{title.lower()}.test", name=title)
		setting = CustomSetting.objects.create(site=site, title=title, api_public=api_public)
		if with_scope:
			setting.scope_subjects.add(self.subject)
		return site, setting

	def test_public_site_with_scope_gets_enabled_and_three_prompts(self):
		site, setting = self._make_setting(title="Public Scoped", api_public=True)

		self._run_seed()

		setting.refresh_from_db()
		self.assertTrue(setting.mcp_enabled)
		self.assertEqual(
			set(SiteMcpPrompt.objects.filter(site=site).values_list("name", flat=True)),
			{"research_topic", "recent_trials_for_subject", "author_profile"},
		)

	def test_private_site_with_scope_is_untouched(self):
		site, setting = self._make_setting(title="Private Scoped", api_public=False)

		self._run_seed()

		setting.refresh_from_db()
		self.assertFalse(setting.mcp_enabled)
		self.assertFalse(SiteMcpPrompt.objects.filter(site=site).exists())

	def test_public_site_with_empty_scope_is_untouched(self):
		site, setting = self._make_setting(
			title="Public Empty Scope", api_public=True, with_scope=False
		)

		self._run_seed()

		setting.refresh_from_db()
		self.assertFalse(setting.mcp_enabled)
		self.assertFalse(SiteMcpPrompt.objects.filter(site=site).exists())

	def test_second_run_changes_nothing(self):
		site, setting = self._make_setting(title="Idempotent", api_public=True)

		self._run_seed()
		first_ids = set(SiteMcpPrompt.objects.filter(site=site).values_list("id", flat=True))
		self._run_seed()
		second_ids = set(SiteMcpPrompt.objects.filter(site=site).values_list("id", flat=True))

		self.assertEqual(first_ids, second_ids)
		self.assertEqual(SiteMcpPrompt.objects.filter(site=site).count(), 3)

	def test_edited_prompt_survives_a_rerun(self):
		site, setting = self._make_setting(title="Edited Prompt", api_public=True)

		self._run_seed()
		prompt = SiteMcpPrompt.objects.get(site=site, name="research_topic")
		prompt.title = "My Custom Title"
		prompt.save()

		self._run_seed()

		prompt.refresh_from_db()
		self.assertEqual(prompt.title, "My Custom Title")


class SeedMcpPromptsCommandTests(TestCase):
	def setUp(self):
		self.site = Site.objects.create(domain="command-seed.test", name="Command Seed")
		self.setting = CustomSetting.objects.create(site=self.site, title="Command Seed Setting")

	def test_creates_prompts_for_the_given_site(self):
		call_command("seed_mcp_prompts", site=self.site.pk)
		self.assertEqual(
			set(
				SiteMcpPrompt.objects.filter(site=self.site).values_list("name", flat=True)
			),
			{"research_topic", "recent_trials_for_subject", "author_profile"},
		)

	def test_dry_run_writes_nothing(self):
		call_command("seed_mcp_prompts", site=self.site.pk, dry_run=True)
		self.assertFalse(SiteMcpPrompt.objects.filter(site=self.site).exists())

	def test_rerun_reports_kept(self):
		import io

		call_command("seed_mcp_prompts", site=self.site.pk)
		out = io.StringIO()
		call_command("seed_mcp_prompts", site=self.site.pk, stdout=out)
		self.assertIn("kept", out.getvalue())
		self.assertNotIn("created", out.getvalue())

	def test_unknown_site_raises_command_error(self):
		with self.assertRaises(CommandError):
			call_command("seed_mcp_prompts", site=999999)

	def test_mcp_enabled_is_unchanged(self):
		self.assertFalse(self.setting.mcp_enabled)
		call_command("seed_mcp_prompts", site=self.site.pk)
		self.setting.refresh_from_db()
		self.assertFalse(self.setting.mcp_enabled)
