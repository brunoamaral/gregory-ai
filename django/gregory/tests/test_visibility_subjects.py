"""
Tests for gregory.visibility — the visible_subject_ids() helper.

These tests pin its per-caller behaviour directly (see
gregory/visibility.py's module docstring for how it relates to
visible_org_ids). The Phase 1 acceptance gate -- the equivalence test
asserting visible_subject_ids(anonymous) matches today's
public-organisation rule -- lives in sitesettings/tests.py alongside the
data migration it exercises, and was updated for Phase 3 (see below) to
pass an explicit ?site_id= rather than relying on the old unconditional
public-union default.

Phase 3 of site-scoped API visibility replaced that default with real site
resolution: an anonymous caller now sees exactly ONE resolved api_public
site's scope. Resolving to nothing raises
gregory.site_resolution.NoSiteResolvedError (a DRF 400) only when that
"nothing" is genuine AMBIGUITY -- two or more api_public sites and no
indicator naming one; zero api_public sites is unambiguous (an empty
scope, no error) and is not this exception. See
test_no_site_indicator_raises_once_a_second_public_site_exists below for
the case that does raise, and gregory/tests/test_site_resolution.py for
resolve_anonymous_site()'s own resolution-order tests (?site_id= -> Origin
-> Referer), which this file assumes rather than re-tests.

Run with:
    docker exec gregory python manage.py test gregory.tests.test_visibility_subjects
"""

from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sites.models import Site
from organizations.models import Organization, OrganizationUser

from gregory.models import Team, Subject, OrganizationSite
from gregory.site_resolution import NoSiteResolvedError
from gregory.visibility import visible_subject_ids
from sitesettings.models import CustomSetting
from api.models import APIAccessScheme

User = get_user_model()


def _make_site_with_scope(domain, title, subjects, api_public):
	"""Create a Site + CustomSetting with scope_subjects pre-populated
	(mirroring post-migration state, not the sitemap_subjects seed)."""
	site = Site.objects.create(domain=domain, name=title)
	setting = CustomSetting.objects.create(
		site=site, title=title, api_public=api_public
	)
	setting.scope_subjects.set(subjects)
	return site, setting


class VisibleSubjectIdsAnonymousTest(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.org = Organization.objects.create(name="Org", slug="vsi-anon-org")
		self.team = Team.objects.create(
			organization=self.org, name="Team", slug="vsi-anon-team"
		)
		self.pub_subject = Subject.objects.create(
			subject_name="Public", subject_slug="vsi-anon-pub", team=self.team
		)
		self.priv_subject = Subject.objects.create(
			subject_name="Private", subject_slug="vsi-anon-priv", team=self.team
		)

		self.pub_site, _ = _make_site_with_scope(
			"vsi-anon-pub.test", "Pub", [self.pub_subject], api_public=True
		)
		self.priv_site, _ = _make_site_with_scope(
			"vsi-anon-priv.test", "Priv", [self.priv_subject], api_public=False
		)

	def _anon_request(self, **extra):
		req = self.factory.get("/", **extra)
		req.user = AnonymousUser()
		return req

	def _anon_request_for_pub_site(self):
		"""An anonymous request resolved to self.pub_site via ?site_id= --
		the explicit, unambiguous way to ask for one site's scope under
		Phase 3 site resolution. See gregory/tests/test_site_resolution.py
		for the ?site_id=/Origin/Referer resolution order itself."""
		return self._anon_request(data={"site_id": str(self.pub_site.pk)})

	def test_no_site_indicator_falls_back_to_the_sole_public_site(self):
		"""Amended 2026-09-10: with exactly one api_public site (pub_site),
		the public union just IS its scope, so an anonymous caller naming no
		site gets it automatically rather than a 400 -- see
		test_no_site_indicator_raises_once_a_second_public_site_exists below
		for where the hard failure actually kicks in."""
		result = visible_subject_ids(self._anon_request())
		self.assertIn(self.pub_subject.id, result)
		self.assertNotIn(self.priv_subject.id, result)

	def test_no_site_indicator_raises_once_a_second_public_site_exists(self):
		"""There is no unscoped mode once the public union is ambiguous: a
		second api_public site makes "serve everything public" mean
		"silently blend two sites' content", so this is where resolution
		fails closed -- see gregory/site_resolution.py."""
		Subject.objects.create(
			subject_name="Second Public",
			subject_slug="vsi-anon-second-pub",
			team=self.team,
		)
		_make_site_with_scope(
			"vsi-anon-pub-2.test", "Pub 2", [], api_public=True
		)
		with self.assertRaises(NoSiteResolvedError):
			visible_subject_ids(self._anon_request())

	def test_resolved_public_site_sees_its_own_scope_only(self):
		result = visible_subject_ids(self._anon_request_for_pub_site())
		self.assertIn(self.pub_subject.id, result)
		self.assertNotIn(self.priv_subject.id, result)

	def test_a_private_settings_row_on_the_resolved_site_does_not_leak_its_scope(self):
		"""CustomSetting.site is a plain FK, not OneToOne -- pub_site can
		carry a SECOND, private settings row alongside the public one that
		made it resolvable at all. The private row's own scope_subjects must
		not be unioned into this anonymous response just because it shares
		a site_id with the public row -- the query must still filter on
		api_public=True, not site_id alone."""
		second_row_subject = Subject.objects.create(
			subject_name="Second Row Private",
			subject_slug="vsi-anon-second-row-private",
			team=self.team,
		)
		second_row = CustomSetting.objects.create(
			site=self.pub_site, title="Pub Site Private Row", api_public=False
		)
		second_row.scope_subjects.add(second_row_subject)

		result = visible_subject_ids(self._anon_request_for_pub_site())
		self.assertIn(self.pub_subject.id, result)
		self.assertNotIn(second_row_subject.id, result)

	def test_spoofed_private_origin_falls_back_to_the_sole_public_site(self):
		"""The security property Phase 3 exists to preserve: Origin is
		client-controlled, but resolution only ever considers api_public
		sites, so claiming to come from a private site's domain can never
		grant that site's scope. With exactly one api_public site here, the
		fallback resolves to it (not a 400 -- amended 2026-09-10), but
		crucially NEVER to the private site the Origin claimed."""
		request = self._anon_request(HTTP_ORIGIN=f"https://{self.priv_site.domain}")
		result = visible_subject_ids(request)
		self.assertIn(self.pub_subject.id, result)
		self.assertNotIn(self.priv_subject.id, result)

	def test_overlapping_scope_is_public_even_if_a_private_site_also_lists_it(self):
		"""Overlap is allowed by design: a subject in a resolved public
		site's scope is visible, even if a private site also contains it --
		not a reason to hide it."""
		shared_subject = Subject.objects.create(
			subject_name="Shared", subject_slug="vsi-anon-shared", team=self.team
		)
		CustomSetting.objects.get(site=self.priv_site).scope_subjects.add(
			shared_subject
		)
		CustomSetting.objects.get(site=self.pub_site).scope_subjects.add(
			shared_subject
		)

		result = visible_subject_ids(self._anon_request_for_pub_site())
		self.assertIn(shared_subject.id, result)

	def test_subject_in_no_sites_scope_is_invisible(self):
		"""Fail-closed: a subject owned by a team but never added to any
		site's scope_subjects (the 'internal research' case) never appears
		to an anonymous caller, even though its team belongs to the same
		organisation as a public site."""
		internal_subject = Subject.objects.create(
			subject_name="Internal", subject_slug="vsi-anon-internal", team=self.team
		)
		result = visible_subject_ids(self._anon_request_for_pub_site())
		self.assertNotIn(internal_subject.id, result)


class VisibleSubjectIdsAuthenticatedUserTest(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.user = User.objects.create_user(username="vsiuser", password="pw")

		self.org = Organization.objects.create(name="Org U", slug="vsi-auth-org")
		self.team = Team.objects.create(
			organization=self.org, name="Team U", slug="vsi-auth-team"
		)
		self.owned_subject = Subject.objects.create(
			subject_name="Owned", subject_slug="vsi-auth-owned", team=self.team
		)

		self.other_org = Organization.objects.create(
			name="Other Org U", slug="vsi-auth-other-org"
		)
		self.other_team = Team.objects.create(
			organization=self.other_org, name="Other Team U", slug="vsi-auth-other-team"
		)
		self.other_subject = Subject.objects.create(
			subject_name="Other", subject_slug="vsi-auth-other", team=self.other_team
		)

		# Org owns one site to start. Below, a second-site test adds another
		# -- this is exactly the arrangement the org-level flag couldn't
		# express (see the spec's "use case this exists for").
		self.site_a, _ = _make_site_with_scope(
			"vsi-auth-a.test", "A", [self.owned_subject], api_public=False
		)
		OrganizationSite.objects.create(
			organization=self.org, site=self.site_a, is_default=True
		)

		self.other_site, _ = _make_site_with_scope(
			"vsi-auth-other.test", "Other", [self.other_subject], api_public=False
		)
		OrganizationSite.objects.create(
			organization=self.other_org, site=self.other_site, is_default=True
		)

	def _authed_request(self):
		req = self.factory.get("/")
		req.user = self.user
		return req

	def test_user_with_no_membership_sees_nothing(self):
		result = visible_subject_ids(self._authed_request())
		self.assertEqual(result, set())

	def test_user_sees_scope_of_every_site_their_org_owns(self):
		OrganizationUser.objects.create(organization=self.org, user=self.user)
		result = visible_subject_ids(self._authed_request())
		self.assertIn(self.owned_subject.id, result)
		self.assertNotIn(self.other_subject.id, result)

	def test_user_sees_a_second_site_owned_by_the_same_org(self):
		"""OrganizationSite is a through table, not a single FK -- an org
		can own several sites, and a member sees all of them, not just the
		default one."""
		second_site, second_setting = _make_site_with_scope(
			"vsi-auth-second.test", "Second", [], api_public=False
		)
		OrganizationSite.objects.create(
			organization=self.org, site=second_site, is_default=False
		)
		extra_subject = Subject.objects.create(
			subject_name="Extra", subject_slug="vsi-auth-extra", team=self.team
		)
		second_setting.scope_subjects.add(extra_subject)

		OrganizationUser.objects.create(organization=self.org, user=self.user)
		result = visible_subject_ids(self._authed_request())
		self.assertIn(self.owned_subject.id, result)
		self.assertIn(extra_subject.id, result)


class VisibleSubjectIdsAPIKeyTest(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.org = Organization.objects.create(name="Org K", slug="vsi-key-org")
		self.team = Team.objects.create(
			organization=self.org, name="Team K", slug="vsi-key-team"
		)
		self.bound_subject = Subject.objects.create(
			subject_name="Bound", subject_slug="vsi-key-bound", team=self.team
		)
		self.other_subject = Subject.objects.create(
			subject_name="Not Bound", subject_slug="vsi-key-other", team=self.team
		)

		self.site, _ = _make_site_with_scope(
			"vsi-key-site.test", "Key Site", [self.bound_subject], api_public=False
		)
		self.other_site, _ = _make_site_with_scope(
			"vsi-key-other-site.test",
			"Other Site",
			[self.other_subject],
			api_public=False,
		)

		# A key's site must belong to the key's organisation -- register the
		# ownership these fixtures rely on. Without OrganizationSite rows the
		# binding is unverifiable and visible_subject_ids fails closed.
		OrganizationSite.objects.create(
			organization=self.org, site=self.site, is_default=True
		)
		OrganizationSite.objects.create(
			organization=self.org, site=self.other_site
		)

		self.scheme = APIAccessScheme.objects.create(
			client_name="Bound Key",
			client_contacts="a@b.com",
			organization=self.org,
			site=self.site,
		)
		self.unbound_scheme = APIAccessScheme.objects.create(
			client_name="Unbound Key",
			client_contacts="a@b.com",
			organization=self.org,
		)

	def _key_request(self, scheme):
		req = self.factory.get(
			"/", HTTP_AUTHORIZATION=scheme.api_key, REMOTE_ADDR="127.0.0.1"
		)
		req.user = AnonymousUser()
		return req

	def test_key_sees_exactly_its_bound_sites_scope(self):
		result = visible_subject_ids(self._key_request(self.scheme))
		self.assertEqual(result, {self.bound_subject.id})

	def test_key_sees_its_private_sites_scope_even_though_not_public(self):
		"""A key grants its site's scope whether or not the site is
		api_public -- that is the whole point of a site-bound credential: a
		private site's own frontend authenticates and reads its own
		scope."""
		self.assertFalse(CustomSetting.objects.get(site=self.site).api_public)
		result = visible_subject_ids(self._key_request(self.scheme))
		self.assertIn(self.bound_subject.id, result)

	def test_key_whose_site_is_not_owned_by_its_org_sees_nothing(self):
		"""A key's site must belong to the key's organisation.

		`organization` and `site` both exist and are independently editable
		until Phase 4 retires the former, so a mismatched pair is reachable
		through the admin. It must fail closed rather than hand this
		credential another organisation's subject scope."""
		foreign_org = Organization.objects.create(
			name="Foreign Co", slug="vsi-key-foreign-org"
		)
		foreign_team = Team.objects.create(
			organization=foreign_org, name="Foreign", slug="vsi-key-foreign-team"
		)
		foreign_subject = Subject.objects.create(
			subject_name="Foreign",
			subject_slug="vsi-key-foreign-subj",
			team=foreign_team,
		)
		foreign_site, _ = _make_site_with_scope(
			"vsi-key-foreign.test", "Foreign Site", [foreign_subject], api_public=False
		)
		OrganizationSite.objects.create(
			organization=foreign_org, site=foreign_site, is_default=True
		)

		# Point this organisation's key at a site another organisation owns.
		self.scheme.site = foreign_site
		self.scheme.save(update_fields=["site"])

		result = visible_subject_ids(self._key_request(self.scheme))
		self.assertEqual(result, set())
		self.assertNotIn(foreign_subject.id, result)

	def test_key_with_no_site_yet_sees_nothing(self):
		"""Phase 1 does not yet enforce that every key has a site. An
		unbacked key must fail closed rather than fall through to some
		other scope."""
		result = visible_subject_ids(self._key_request(self.unbound_scheme))
		self.assertEqual(result, set())
