import importlib

from django.apps import apps as django_apps
from django.contrib.auth.models import AnonymousUser
from django.contrib.sites.models import Site
from django.test import RequestFactory, TestCase
from organizations.models import Organization

from api.models import APIAccessScheme
from gregory.models import OrganizationApiSettings, OrganizationSite, Subject, Team
from gregory.visibility import visible_subject_ids

from .admin import CustomSettingAdminForm
from .models import CustomSetting
from .utils import author_page_base


class SenderNameFallbackTests(TestCase):
	"""The senders use `customsettings.sender_name or customsettings.title` as
	the From display name. These tests pin that fallback contract so the field
	stays backwards-compatible for sites that never set sender_name."""

	def setUp(self):
		self.site = Site.objects.create(domain="example.test", name="Example")

	def _make(self, **kwargs):
		defaults = {"site": self.site, "title": "Fallback Title"}
		defaults.update(kwargs)
		# title is unique=True, ensure each instance has a distinct one
		return CustomSetting.objects.create(**defaults)

	def test_sender_name_defaults_to_blank(self):
		cs = self._make(title="Blank Default Site")
		self.assertEqual(cs.sender_name, "")

	def test_blank_sender_name_falls_back_to_title(self):
		cs = self._make(title="My Project")
		resolved = cs.sender_name or cs.title
		self.assertEqual(resolved, "My Project")

	def test_set_sender_name_overrides_title(self):
		cs = self._make(title="Internal Project Name", sender_name="Public Brand")
		resolved = cs.sender_name or cs.title
		self.assertEqual(resolved, "Public Brand")


class AuthorPageBaseTests(TestCase):
	"""author_page_base resolves the base URL for a site's author profile
	pages, or "" when the site doesn't publish them - see
	docs/06-organisations-teams-and-sites.md#author-profile-page-links."""

	def setUp(self):
		self.site = Site.objects.create(domain="brain-regeneration.com", name="BR")

	def _make(self, **kwargs):
		defaults = {"site": self.site, "title": "Some Title"}
		defaults.update(kwargs)
		return CustomSetting.objects.create(**defaults)

	def test_flag_off_returns_empty(self):
		cs = self._make(title="Flag Off", has_author_pages=False)
		self.assertEqual(author_page_base(self.site, cs), "")

	def test_flag_on_returns_base_url(self):
		cs = self._make(title="Flag On", has_author_pages=True)
		self.assertEqual(
			author_page_base(self.site, cs),
			"https://brain-regeneration.com/authors",
		)

	def test_no_custom_setting_returns_empty(self):
		site = Site.objects.create(domain="no-settings.test", name="No Settings")
		self.assertEqual(author_page_base(site), "")

	def test_blank_domain_returns_empty(self):
		site = Site.objects.create(domain="   ", name="Blank Domain")
		cs = CustomSetting.objects.create(
			site=site, title="Blank Domain Site", has_author_pages=True
		)
		self.assertEqual(author_page_base(site, cs), "")

	def test_localhost_uses_http_scheme(self):
		site = Site.objects.create(domain="localhost", name="Localhost")
		cs = CustomSetting.objects.create(
			site=site, title="Localhost Site", has_author_pages=True
		)
		self.assertEqual(author_page_base(site, cs), "http://localhost/authors")

	def test_lowest_setting_id_wins_when_multiple_rows(self):
		first = self._make(title="First Row", has_author_pages=True)
		self._make(title="Second Row", has_author_pages=False)
		self.assertEqual(
			author_page_base(self.site),
			"https://brain-regeneration.com/authors",
		)
		self.assertEqual(
			CustomSetting.objects.filter(site=self.site)
			.order_by("setting_id")
			.first()
			.setting_id,
			first.setting_id,
		)

	def test_missing_site_returns_empty(self):
		self.assertEqual(author_page_base(None), "")


class SitemapTrialStatusesAdminFormTests(TestCase):
	"""The tickbox widget writes into a Postgres ArrayField. A
	MultipleChoiceField hands back a list of strings, which ArrayField
	accepts — pin that round trip so a widget change can't silently start
	storing something the sitemap query won't match."""

	def setUp(self):
		self.site = Site.objects.create(domain="statuses.test", name="Statuses")

	def _form(self, statuses):
		data = {
			"site": self.site.pk,
			"title": "Statuses Site",
			"sender_email_prefix": "gregory",
			"sitemap_trial_statuses": statuses,
		}
		return CustomSettingAdminForm(data=data)

	def test_defaults_to_empty_list(self):
		cs = CustomSetting.objects.create(site=self.site, title="Default Statuses")
		self.assertEqual(cs.sitemap_trial_statuses, [])

	def test_selected_statuses_round_trip_as_a_list(self):
		form = self._form(["recruiting", "not_yet_recruiting"])
		self.assertTrue(form.is_valid(), form.errors)
		saved = form.save()
		saved.refresh_from_db()
		self.assertEqual(
			sorted(saved.sitemap_trial_statuses),
			["not_yet_recruiting", "recruiting"],
		)

	def test_no_selection_saves_an_empty_list_not_a_blank_string(self):
		form = self._form([])
		self.assertTrue(form.is_valid(), form.errors)
		saved = form.save()
		saved.refresh_from_db()
		# [""] would make the sitemap filter match nothing at all.
		self.assertEqual(saved.sitemap_trial_statuses, [])

	def test_status_outside_the_enum_is_rejected(self):
		form = self._form(["definitely_not_a_status"])
		self.assertFalse(form.is_valid())
		self.assertIn("sitemap_trial_statuses", form.errors)


class SeedSiteVisibilityScopeMigrationTests(TestCase):
	"""Tests for sitesettings/migrations/0019_seed_site_visibility_scope.py
	-- the Phase 1 data migration for the site-scoped API visibility
	project. Its five steps (seed scope_subjects, publish
	brain-regeneration.com, enable its feeds, seed Team.api_listed,
	backfill APIAccessScheme.site) are only correct together; see the
	migration file's module docstring.

	pytest runs with --nomigrations (pytest.ini), so the test database is
	built straight from current model state and this migration is never
	actually applied by the test runner. Calling its function directly
	against the live model registry -- the same pattern used in
	api/tests/test_apiaccessscheme_org_required.py for migration 0004 -- is
	how it gets exercised at all.

	test_equivalence_anonymous_subject_scope_matches_todays_public_org_rule
	is the Phase 1 acceptance gate: with the migration applied,
	visible_subject_ids() for an anonymous caller must equal every subject
	visible under today's public-organisation rule. If it does not, the
	migration is wrong -- every later phase depends on this holding.

	This fixture is deliberately "fully curated": every subject a public
	org's team owns is also in that org's site's sitemap_subjects (and
	therefore its scope_subjects, once seeded). That precondition is what
	makes the equivalence exact. The one place production data does NOT
	meet it -- subject 15 (Dihydroartemisinin), owned by a
	brain-regeneration.com team but deliberately left out of its sitemap
	-- is tested separately below in
	UncuratedPublicOrgSubjectStaysExcludedTests, since the spec documents
	that exclusion as intended ("the design working, not a cost"), not a
	migration defect. Mixing that case into this fixture would make a
	correct migration fail an "equivalence" test that was never meant to
	cover it.
	"""

	def _run_migration(self):
		mod = importlib.import_module(
			"sitesettings.migrations.0019_seed_site_visibility_scope"
		)
		mod.seed_scope_and_backfill(django_apps, schema_editor=None)

	def setUp(self):
		self.factory = RequestFactory()

		# --- brain-regeneration.com itself: the ONE domain the migration
		# hardcodes as api_public/rss_enabled. Its team owns two subjects,
		# both curated into the site's sitemap -- i.e. fully curated, see
		# the class docstring for why that matters here.
		self.br_org = Organization.objects.create(
			name="Brain Regeneration Co", slug="ssv-br-co"
		)
		OrganizationApiSettings.objects.filter(organization=self.br_org).update(
			make_api_public=True
		)
		self.br_site = Site.objects.create(
			domain="brain-regeneration.com", name="Brain Regeneration"
		)
		OrganizationSite.objects.create(
			organization=self.br_org, site=self.br_site, is_default=True
		)
		self.br_team = Team.objects.create(
			organization=self.br_org, name="BR Team", slug="ssv-br-team"
		)
		self.br_subject_a = Subject.objects.create(
			subject_name="BR Subject A", subject_slug="ssv-br-a", team=self.br_team
		)
		self.br_subject_b = Subject.objects.create(
			subject_name="BR Subject B", subject_slug="ssv-br-b", team=self.br_team
		)
		self.br_setting = CustomSetting.objects.create(
			site=self.br_site, title="BR Setting"
		)
		self.br_setting.sitemap_subjects.set([self.br_subject_a, self.br_subject_b])

		# --- A second organisation that is ALSO make_api_public=True today
		# but owns no team and therefore no subjects -- mirrors the real
		# prod-synced database, where org 1 (Human Singularity) is flagged
		# public alongside Brain Regeneration but owns nothing, so it
		# cannot perturb the equivalence below. Proves the equivalence
		# isn't holding by coincidence of "exactly one public org".
		self.empty_public_org = Organization.objects.create(
			name="Empty Public Co", slug="ssv-empty-public-co"
		)
		OrganizationApiSettings.objects.filter(
			organization=self.empty_public_org
		).update(make_api_public=True)

		# --- A private organisation with its own site and subject. ---
		self.private_org = Organization.objects.create(
			name="Private Co", slug="ssv-private-co"
		)
		OrganizationApiSettings.objects.filter(organization=self.private_org).update(
			make_api_public=False
		)
		self.private_site = Site.objects.create(
			domain="ssv-private.example.test", name="Private"
		)
		OrganizationSite.objects.create(
			organization=self.private_org, site=self.private_site, is_default=True
		)
		self.private_team = Team.objects.create(
			organization=self.private_org, name="Private Team", slug="ssv-private-team"
		)
		self.private_subject = Subject.objects.create(
			subject_name="Private",
			subject_slug="ssv-private-subj",
			team=self.private_team,
		)
		self.private_setting = CustomSetting.objects.create(
			site=self.private_site, title="Private Setting"
		)
		self.private_setting.sitemap_subjects.set([self.private_subject])

		# A subject with no team at all -- must never appear anywhere.
		self.orphan_subject = Subject.objects.create(
			subject_name="Orphan", subject_slug="ssv-orphan", team=None
		)

		# One API key per org that owns a site, pre-migration (no site set
		# yet).
		self.br_org_key = APIAccessScheme.objects.create(
			client_name="BR Key", client_contacts="a@b.com", organization=self.br_org
		)
		self.private_org_key = APIAccessScheme.objects.create(
			client_name="Private Key",
			client_contacts="a@b.com",
			organization=self.private_org,
		)

	def test_equivalence_anonymous_subject_scope_matches_todays_public_org_rule(self):
		"""The acceptance gate (plan §1.4): with the migration applied,
		visible_subject_ids(anonymous) == every subject visible under
		today's public-organisation rule.

		Phase 3 (site resolution, see gregory/site_resolution.py) replaced
		the anonymous default with real resolution, but a caller naming no
		site still gets exactly this fixture's public union automatically
		-- br_site is the only api_public site here, so that union IS its
		scope, and resolve_anonymous_site() serves it directly rather than
		refusing. The bare "/" request below is therefore still the right
		way to exercise this equivalence unchanged.
		"""
		self._run_migration()

		req = self.factory.get("/")
		req.user = AnonymousUser()
		actual = visible_subject_ids(req)

		todays_public_org_rule = set(
			Subject.objects.filter(
				team__organization__api_settings__make_api_public=True
			).values_list("id", flat=True)
		)
		self.assertEqual(actual, todays_public_org_rule)
		# Concretely: the two subjects seeded into brain-regeneration.com's
		# scope and nothing else -- not the private org's subject, not the
		# orphan, and nothing from the other public-but-empty org (it owns
		# no subjects to contribute).
		self.assertEqual(actual, {self.br_subject_a.id, self.br_subject_b.id})

	def test_public_site_scope_excludes_a_private_orgs_subject(self):
		"""Step 3b: a PUBLIC site's scope is narrowed to publicly-owned
		subjects.

		sitemap_subjects is not the old public subject set — a sitemap may
		name a subject owned by a private organisation's team, and
		rss/sitemaps.py drops it by re-checking team ownership. Seeding it
		into a public site's scope would publish, through the anonymous
		rule, something the old rule kept private. Private sites are NOT
		narrowed — their scope is reachable only via their own key, and
		stripping it would remove access that key is meant to have."""
		# Put the private org's subject into brain-regeneration's sitemap.
		self.br_setting.sitemap_subjects.add(self.private_subject)

		self._run_migration()

		br_scope = set(
			self.br_setting.scope_subjects.values_list("id", flat=True)
		)
		self.assertNotIn(self.private_subject.id, br_scope)
		self.assertEqual(
			br_scope, {self.br_subject_a.id, self.br_subject_b.id}
		)

		# The private site keeps its own subject — not narrowed.
		self.assertEqual(
			set(self.private_setting.scope_subjects.values_list("id", flat=True)),
			{self.private_subject.id},
		)

		# And the anonymous rule still matches the old one (Phase 3's
		# resolve_anonymous_site() serves br_site's scope automatically here
		# -- see the equivalence test above).
		req = self.factory.get("/")
		req.user = AnonymousUser()
		self.assertNotIn(self.private_subject.id, visible_subject_ids(req))

	def test_all_api_keys_resolve_to_a_site(self):
		self._run_migration()
		self.br_org_key.refresh_from_db()
		self.private_org_key.refresh_from_db()
		self.assertEqual(self.br_org_key.site_id, self.br_site.id)
		self.assertEqual(self.private_org_key.site_id, self.private_site.id)

	def test_team_api_listed_seeded_for_teams_owning_a_publicly_scoped_subject(self):
		self._run_migration()
		self.br_team.refresh_from_db()
		self.private_team.refresh_from_db()
		self.assertTrue(self.br_team.api_listed)
		self.assertFalse(self.private_team.api_listed)

	def test_scope_subjects_seeded_from_sitemap_subjects(self):
		self._run_migration()
		self.br_setting.refresh_from_db()
		self.private_setting.refresh_from_db()
		self.assertEqual(
			set(self.br_setting.scope_subjects.values_list("id", flat=True)),
			{self.br_subject_a.id, self.br_subject_b.id},
		)
		self.assertEqual(
			set(self.private_setting.scope_subjects.values_list("id", flat=True)),
			{self.private_subject.id},
		)

	def test_api_public_and_rss_enabled_seeded_for_brain_regeneration_only(self):
		self._run_migration()
		self.br_setting.refresh_from_db()
		self.private_setting.refresh_from_db()
		self.assertTrue(self.br_setting.api_public)
		self.assertTrue(self.br_setting.rss_enabled)
		self.assertFalse(self.private_setting.api_public)
		self.assertFalse(self.private_setting.rss_enabled)

	def test_organisation_with_no_default_site_raises_rather_than_guessing(self):
		"""A key mapped to nothing is a dead frontend -- the migration must
		fail loudly rather than leave APIAccessScheme.site null."""
		org = Organization.objects.create(
			name="No Default Site Org", slug="ssv-no-default-org"
		)
		# No OrganizationSite row at all for this org -- not even a
		# non-default one.
		APIAccessScheme.objects.create(
			client_name="Orphan Key", client_contacts="a@b.com", organization=org
		)
		with self.assertRaises(Exception):
			self._run_migration()

	def test_organisation_with_a_non_default_site_only_still_raises(self):
		"""is_default is a nullable convention, not a guaranteed row -- an
		org with a site that is simply never marked default must not be
		treated as resolvable."""
		org = Organization.objects.create(
			name="No Default Flag Org", slug="ssv-no-default-flag-org"
		)
		site = Site.objects.create(domain="ssv-no-default.example.test", name="ND")
		OrganizationSite.objects.create(organization=org, site=site, is_default=False)
		APIAccessScheme.objects.create(
			client_name="Unflagged Key", client_contacts="a@b.com", organization=org
		)
		with self.assertRaises(Exception):
			self._run_migration()


class UncuratedPublicOrgSubjectStaysExcludedTests(TestCase):
	"""Pins the one documented, intentional divergence between the old
	organisation-level rule and the new site-scope rule: a subject owned by
	a public org's team, but never curated into that org's site's
	sitemap_subjects, is visible under today's rule
	(team__organization__api_settings__make_api_public=True says nothing
	about curation) and becomes invisible after this migration.

	This is exactly the real "subject 15 / Dihydroartemisinin" case
	documented in the site-scoped API visibility spec's "Subject 15 is the
	design working, not a cost" section: internal research owned by a
	public org's team, deliberately kept out of every site's scope. A
	team-scoped model publishes it by accident (the team's org happens to
	be public); the subject-scope model does not, because the org's site
	never curated it in. That is why this behaviour is asserted here as a
	requirement, not treated as a bug the migration should avoid.

	Deliberately a separate TestCase from
	SeedSiteVisibilityScopeMigrationTests: that class's equivalence test
	assumes full curation (every team-owned subject is also
	sitemap-curated) specifically so the exact-equality assertion means
	something. This class is the other half -- the documented exception to
	that precondition.
	"""

	def _run_migration(self):
		mod = importlib.import_module(
			"sitesettings.migrations.0019_seed_site_visibility_scope"
		)
		mod.seed_scope_and_backfill(django_apps, schema_editor=None)

	def test_subject_owned_by_public_org_but_uncurated_is_excluded_after_migration(
		self,
	):
		org = Organization.objects.create(name="Curating Co", slug="ssv-uncurated-org")
		OrganizationApiSettings.objects.filter(organization=org).update(
			make_api_public=True
		)
		site = Site.objects.create(domain="brain-regeneration.com", name="BR")
		OrganizationSite.objects.create(organization=org, site=site, is_default=True)
		team = Team.objects.create(
			organization=org, name="Curating Team", slug="ssv-uncurated-team"
		)
		curated_subject = Subject.objects.create(
			subject_name="Curated", subject_slug="ssv-uncurated-curated", team=team
		)
		uncurated_subject = Subject.objects.create(
			subject_name="Dihydroartemisinin-shaped",
			subject_slug="ssv-uncurated-internal",
			team=team,
		)
		setting = CustomSetting.objects.create(site=site, title="Curating Setting")
		setting.sitemap_subjects.set([curated_subject])  # uncurated_subject left out

		# Today: both subjects' team belongs to a public org, so both are
		# visible under the org rule (e.g. /subjects/?team_id=... today).
		todays_public_org_rule = set(
			Subject.objects.filter(
				team__organization__api_settings__make_api_public=True
			).values_list("id", flat=True)
		)
		self.assertEqual(
			todays_public_org_rule, {curated_subject.id, uncurated_subject.id}
		)

		self._run_migration()

		# Phase 3's resolve_anonymous_site() serves `site`'s scope
		# automatically here -- it is the only api_public site this fixture
		# creates, so the public union just IS its scope (see
		# gregory/site_resolution.py).
		req = RequestFactory().get("/")
		req.user = AnonymousUser()
		after_migration = visible_subject_ids(req)

		self.assertIn(curated_subject.id, after_migration)
		self.assertNotIn(uncurated_subject.id, after_migration)
