# Generated manually 2026-09-19
#
# MCP multi-tenancy Phase 2, PR B -- see MCP-MULTI-TENANCY-PHASE-2-PLAN.md
# (local planning doc, not committed), decision 1.
#
# For every CustomSetting row that is api_public with a non-empty
# scope_subjects, turn mcp_enabled on and seed the three built-in prompts
# (sitesettings.mcp_defaults.DEFAULT_MCP_PROMPTS) as editable SiteMcpPrompt
# rows. In production that is brain-regeneration.com only. New sites, private
# sites, and public sites with an empty scope are left untouched -- not
# eligible, per decision 1.
#
# get_or_create() never updates an existing row, so a prompt someone has
# already edited survives a re-run of this migration (e.g. after a
# --fake-initial replay) or of the seed_mcp_prompts management command
# (PR B3), which follows the same rule.
#
# Importing mcp_defaults here is safe: it holds plain data and imports no
# models. Editing DEFAULT_MCP_PROMPTS later does not re-run this migration or
# change rows it already created -- see that module's docstring.
#
# Must also run cleanly on an empty database: CI runs `migrate` against one,
# where CustomSetting.objects.none() matches and the loop below is a no-op.

from django.db import migrations

from sitesettings.mcp_defaults import DEFAULT_MCP_PROMPTS


def seed(apps, schema_editor):
	CustomSetting = apps.get_model("sitesettings", "CustomSetting")
	SiteMcpPrompt = apps.get_model("sitesettings", "SiteMcpPrompt")

	eligible = CustomSetting.objects.filter(
		api_public=True, scope_subjects__isnull=False
	).distinct()

	for custom_setting in eligible:
		custom_setting.mcp_enabled = True
		custom_setting.save(update_fields=["mcp_enabled"])

		for prompt in DEFAULT_MCP_PROMPTS:
			SiteMcpPrompt.objects.get_or_create(
				site_id=custom_setting.site_id,
				name=prompt["name"],
				defaults={
					"title": prompt["title"],
					"description": prompt["description"],
					"template": prompt["template"],
					"arguments": prompt["arguments"],
					"ordering": prompt["ordering"],
				},
			)


class Migration(migrations.Migration):

	dependencies = [
		("sitesettings", "0021_customsetting_mcp_description_and_more"),
	]

	operations = [
		migrations.RunPython(seed, migrations.RunPython.noop),
	]
