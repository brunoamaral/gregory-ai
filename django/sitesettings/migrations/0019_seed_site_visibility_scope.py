# Generated manually 2026-09-09
#
# Site-scoped API visibility, Phase 1 data migration. Five steps that are
# only correct together -- see SITE-API-VISIBILITY-PLAN.md §1.2 (local
# planning doc, not committed). Splitting them across migrations would leave
# an intermediate state where scope_subjects is seeded but api_public is
# still False everywhere, which would take the public API dark.
#
#   1. Seed CustomSetting.scope_subjects from sitemap_subjects, for every
#      site. This makes the cutover a no-op: brain-regeneration.com already
#      curates its six sitemap_subjects correctly, and gregory-ms.com (the
#      decommissioned site) has none.
#   2. Set api_public=True for brain-regeneration.com only. api_public
#      defaults to False, so without this step every site is private and
#      the public API goes dark.
#   3. Set rss_enabled=True for brain-regeneration.com only, for the same
#      reason -- otherwise existing feed consumers break silently.
#   4. Set Team.api_listed=True for every team that owns a subject in a
#      site with api_public=True. Today that resolves to teams 1, 4, 5, 6
#      -- all four of brain-regeneration's teams, since one organisation
#      owns every subject currently in scope.
#   5. Map each APIAccessScheme to its organisation's default site, via
#      OrganizationSite where is_default=True. Raises if an organisation
#      has no default site rather than guessing -- a key mapped to nothing
#      is a dead frontend. Verified derivable for all 5 current keys
#      (all belong to org 6, Brain Regeneration, whose default site is
#      brain-regeneration.com).
#
# Self-contained: uses apps.get_model() throughout, no live model imports.

from django.db import migrations

BRAIN_REGENERATION_DOMAIN = "brain-regeneration.com"


def seed_scope_and_backfill(apps, schema_editor):
	CustomSetting = apps.get_model("sitesettings", "CustomSetting")
	Team = apps.get_model("gregory", "Team")
	Subject = apps.get_model("gregory", "Subject")
	OrganizationSite = apps.get_model("gregory", "OrganizationSite")
	APIAccessScheme = apps.get_model("api", "APIAccessScheme")

	# --- Step 1: seed scope_subjects from sitemap_subjects, every site. ---
	for custom_setting in CustomSetting.objects.all():
		custom_setting.scope_subjects.set(custom_setting.sitemap_subjects.all())

	# --- Steps 2 & 3: brain-regeneration.com is public and feeds today. ---
	# A no-op on any database without that domain (e.g. a fresh install, or
	# CI's empty test database) -- .update() on zero rows changes nothing.
	CustomSetting.objects.filter(site__domain=BRAIN_REGENERATION_DOMAIN).update(
		api_public=True, rss_enabled=True
	)

	# --- Step 4: Team.api_listed for teams owning a subject in a public
	# site's scope. ---
	public_subject_ids = set()
	for custom_setting in CustomSetting.objects.filter(api_public=True):
		public_subject_ids |= set(
			custom_setting.scope_subjects.values_list("id", flat=True)
		)

	listed_team_ids = set(
		Subject.objects.filter(id__in=public_subject_ids, team__isnull=False)
		.values_list("team_id", flat=True)
		.distinct()
	)
	if listed_team_ids:
		Team.objects.filter(id__in=listed_team_ids).update(api_listed=True)

	# --- Step 5: APIAccessScheme.site from the organisation's default site. ---
	default_site_by_org = dict(
		OrganizationSite.objects.filter(is_default=True).values_list(
			"organization_id", "site_id"
		)
	)
	for org_id, site_id in default_site_by_org.items():
		APIAccessScheme.objects.filter(
			organization_id=org_id, site__isnull=True
		).update(site_id=site_id)

	unresolved_orgs = sorted(
		set(
			APIAccessScheme.objects.filter(site__isnull=True).values_list(
				"organization_id", flat=True
			)
		)
	)
	if unresolved_orgs:
		raise Exception(
			"Cannot backfill APIAccessScheme.site: organisation(s) "
			f"{unresolved_orgs} have no default OrganizationSite "
			"(is_default=True). Add one for each before running this "
			"migration -- a key mapped to nothing is a dead frontend, and "
			"this migration will not guess."
		)


class Migration(migrations.Migration):

	dependencies = [
		("sitesettings", "0018_customsetting_api_public_customsetting_rss_enabled_and_more"),
		("gregory", "0096_team_api_listed"),
		("api", "0007_apiaccessscheme_site"),
	]

	operations = [
		migrations.RunPython(seed_scope_and_backfill, migrations.RunPython.noop),
	]
