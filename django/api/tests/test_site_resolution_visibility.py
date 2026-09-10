"""
End-to-end tests for Phase 3 of site-scoped API visibility: anonymous site
resolution and its fail-closed 400, exercised through real HTTP requests
(not direct calls to gregory.visibility.visible_subject_ids -- those are
pinned in gregory/tests/test_visibility_subjects.py and
gregory/tests/test_site_resolution.py).

Covers the acceptance list in PHASE-3-SITE-RESOLUTION-PLAN.md end to end:
  - Two or more api_public sites and no site indicator -> 400, body lists
    the public sites, and that body is byte-for-byte the same list GET
    /sites/ returns (one code path: gregory.site_resolution.public_sites).
  - ?site_id=<public> resolves to that site's own scope.
  - ?site_id=<private> never grants that site's scope.
  - Origin resolves; Referer resolves when Origin is absent; spoofing
    Origin as a private site never grants that site's scope.
  - A site-bound API key ignores a conflicting Origin entirely; so does a
    signed-in member.
  - Vary: Origin is set exactly when the outcome could depend on it.
  - GET /sites/ itself is never gated -- it's the discovery entry point.
  - RSS feeds and sitemaps (which resolve by SITE, not by caller -- Phase
    4b/5) are unaffected by any of this, since they never read
    visible_subject_ids at all.

Run with:
    docker exec gregory python manage.py test api.tests.test_site_resolution_visibility
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.timezone import now
from organizations.models import Organization, OrganizationUser
from rest_framework.test import APIClient

from api.models import APIAccessScheme
from api.tests.visibility_helpers import private_site_publishing, publish_subjects
from gregory.models import Articles, Subject, Team
from sitesettings.models import CustomSetting

User = get_user_model()


class TwoPublicSitesAmbiguityTest(TestCase):
	"""The case Phase 3 exists to guard: two api_public sites, no site
	indicator. Serving the union would silently blend them."""

	def setUp(self):
		self.client = APIClient()
		self.org_a = Organization.objects.create(name="Amb Org A", slug="amb-org-a")
		self.org_b = Organization.objects.create(name="Amb Org B", slug="amb-org-b")
		self.team_a = Team.objects.create(
			organization=self.org_a, name="Amb Team A", slug="amb-team-a"
		)
		self.team_b = Team.objects.create(
			organization=self.org_b, name="Amb Team B", slug="amb-team-b"
		)
		self.subject_a = Subject.objects.create(
			subject_name="Amb Subject A", subject_slug="amb-subj-a", team=self.team_a
		)
		self.subject_b = Subject.objects.create(
			subject_name="Amb Subject B", subject_slug="amb-subj-b", team=self.team_b
		)
		self.site_a = publish_subjects(
			self.subject_a, organization=self.org_a, name="Amb Site A"
		)
		self.site_b = publish_subjects(
			self.subject_b, organization=self.org_b, name="Amb Site B"
		)
		# ArticleFilter's OWN, unrelated ?site_id= filter (Team.site, still
		# using the pre-Phase-6 team->site edge -- see
		# SITE-API-VISIBILITY-SPEC.md's "site_id is broken today") reads the
		# exact same query parameter this test uses for VISIBILITY
		# resolution. Set Team.site to match so that legacy filter doesn't
		# also silently exclude these fixtures and confound what this test
		# is actually checking -- the collision itself is documented and
		# deliberately deferred to Phase 6, not something this test covers.
		self.team_a.site = self.site_a
		self.team_a.save(update_fields=["site"])
		self.team_b.site = self.site_b
		self.team_b.save(update_fields=["site"])
		article = Articles.objects.create(
			title="Amb Article A", link="https://amb.example/a"
		)
		article.subjects.add(self.subject_a)
		article.teams.add(self.team_a)

	def test_no_indicator_returns_400(self):
		resp = self.client.get("/articles/")
		self.assertEqual(resp.status_code, 400)

	def test_400_body_shape_and_content(self):
		resp = self.client.get("/articles/")
		self.assertEqual(resp.data["error"], "No site could be determined for this request.")
		self.assertIn("site_id", resp.data["detail"])
		site_ids = {row["site_id"] for row in resp.data["public_sites"]}
		self.assertEqual(site_ids, {self.site_a.pk, self.site_b.pk})

	def test_400_body_matches_get_sites(self):
		error_resp = self.client.get("/articles/")
		sites_resp = self.client.get("/sites/")
		self.assertEqual(sites_resp.status_code, 200)

		def _key(rows):
			return sorted((r["site_id"], r["domain"], r["name"]) for r in rows)

		self.assertEqual(_key(error_resp.data["public_sites"]), _key(sites_resp.data))

	def test_get_sites_itself_is_never_gated(self):
		"""The discovery endpoint cannot require the thing it exists to
		provide -- it must answer with no site indicator, even amid the
		exact ambiguity that gates every content endpoint."""
		resp = self.client.get("/sites/")
		self.assertEqual(resp.status_code, 200)

	def test_explicit_site_id_resolves_despite_ambiguity(self):
		resp = self.client.get("/articles/", {"site_id": self.site_a.pk})
		self.assertEqual(resp.status_code, 200)
		titles = [a["title"] for a in resp.data["results"]]
		self.assertIn("Amb Article A", titles)

	def test_explicit_site_id_for_the_other_site_excludes_the_first(self):
		resp = self.client.get("/articles/", {"site_id": self.site_b.pk})
		self.assertEqual(resp.status_code, 200)
		titles = [a["title"] for a in resp.data["results"]]
		self.assertNotIn("Amb Article A", titles)

	def test_origin_resolves_to_the_matching_site(self):
		resp = self.client.get("/articles/", HTTP_ORIGIN=f"https://{self.site_a.domain}")
		self.assertEqual(resp.status_code, 200)
		titles = [a["title"] for a in resp.data["results"]]
		self.assertIn("Amb Article A", titles)

	def test_referer_resolves_when_origin_absent(self):
		resp = self.client.get(
			"/articles/",
			HTTP_REFERER=f"https://{self.site_a.domain}/some/page/",
		)
		self.assertEqual(resp.status_code, 200)

	def test_unresolvable_origin_still_400s(self):
		resp = self.client.get("/articles/", HTTP_ORIGIN="https://not-a-registered-site.example")
		self.assertEqual(resp.status_code, 400)

	def test_vary_origin_set_on_the_400(self):
		resp = self.client.get("/articles/")
		self.assertIn("Origin", resp.headers.get("Vary", ""))

	def test_vary_origin_set_when_origin_actually_resolves(self):
		resp = self.client.get("/articles/", HTTP_ORIGIN=f"https://{self.site_a.domain}")
		self.assertIn("Origin", resp.headers.get("Vary", ""))

	def test_vary_origin_not_set_when_explicit_site_id_alone_settles_it(self):
		"""?site_id= short-circuits before Origin is ever consulted, so a
		cache keyed on this response need not vary by it."""
		resp = self.client.get("/articles/", {"site_id": self.site_a.pk})
		self.assertNotIn("Origin", resp.headers.get("Vary", ""))


class SpoofedOriginNarrowingTest(TestCase):
	"""The single most important property in this phase: a caller claiming
	to come from a private site's domain must never be granted that site's
	scope -- narrowing only, never widening."""

	def setUp(self):
		self.client = APIClient()
		self.org = Organization.objects.create(name="Spoof Org", slug="spoof-org")
		self.team = Team.objects.create(
			organization=self.org, name="Spoof Team", slug="spoof-team"
		)
		self.pub_subject = Subject.objects.create(
			subject_name="Spoof Public", subject_slug="spoof-pub", team=self.team
		)
		self.priv_subject = Subject.objects.create(
			subject_name="Spoof Private", subject_slug="spoof-priv", team=self.team
		)
		self.pub_site = publish_subjects(
			self.pub_subject, organization=self.org, name="Spoof Public Site"
		)
		self.priv_site = private_site_publishing(
			self.priv_subject, organization=self.org, name="Spoof Private Site"
		)
		pub_article = Articles.objects.create(
			title="Spoof Public Article", link="https://spoof.example/pub"
		)
		pub_article.subjects.add(self.pub_subject)
		pub_article.teams.add(self.team)
		priv_article = Articles.objects.create(
			title="Spoof Private Article", link="https://spoof.example/priv"
		)
		priv_article.subjects.add(self.priv_subject)
		priv_article.teams.add(self.team)

	def test_spoofed_private_origin_falls_back_to_the_sole_public_site(self):
		"""Exactly one api_public site exists here (pub_site), so the
		fallback resolves to it directly (2026-09-10 spec amendment) --
		but crucially it is the PUBLIC site's content, never the private
		one the Origin claimed."""
		resp = self.client.get(
			"/articles/", HTTP_ORIGIN=f"https://{self.priv_site.domain}"
		)
		self.assertEqual(resp.status_code, 200)
		titles = [a["title"] for a in resp.data["results"]]
		self.assertIn("Spoof Public Article", titles)
		self.assertNotIn("Spoof Private Article", titles)

	def test_private_sites_own_id_never_resolves_via_site_id_either(self):
		resp = self.client.get("/articles/", {"site_id": self.priv_site.pk})
		self.assertEqual(resp.status_code, 200)
		titles = [a["title"] for a in resp.data["results"]]
		self.assertNotIn("Spoof Private Article", titles)


class IdentifiedCallerIgnoresOriginTest(TestCase):
	"""A site-bound key or a signed-in member's scope is decided by the
	credential/membership, never by a conflicting Origin header -- Origin
	resolution is an ANONYMOUS-only mechanism."""

	def setUp(self):
		self.client = APIClient()
		self.my_org = Organization.objects.create(name="Mine Org SR", slug="mine-org-sr")
		self.pub_org = Organization.objects.create(name="Pub Org SR", slug="pub-org-sr")
		self.my_team = Team.objects.create(
			organization=self.my_org, name="Mine Team SR", slug="mine-team-sr"
		)
		self.pub_team = Team.objects.create(
			organization=self.pub_org, name="Pub Team SR", slug="pub-team-sr"
		)
		self.my_subject = Subject.objects.create(
			subject_name="Mine Subject SR", subject_slug="mine-subj-sr", team=self.my_team
		)
		self.pub_subject = Subject.objects.create(
			subject_name="Pub Subject SR", subject_slug="pub-subj-sr", team=self.pub_team
		)
		self.my_site = private_site_publishing(
			self.my_subject, organization=self.my_org, name="Mine Site SR"
		)
		self.pub_site = publish_subjects(
			self.pub_subject, organization=self.pub_org, name="Pub Site SR"
		)
		my_article = Articles.objects.create(
			title="Mine Article SR", link="https://sr.example/mine"
		)
		my_article.subjects.add(self.my_subject)
		my_article.teams.add(self.my_team)

	def test_api_key_bound_to_my_site_ignores_a_conflicting_origin(self):
		scheme = APIAccessScheme.objects.create(
			client_name="SR Key",
			client_contacts="a@b.com",
			organization=self.my_org,
			site=self.my_site,
			ip_addresses="",
			begin_date=now() - timedelta(days=1),
			end_date=now() + timedelta(days=30),
		)
		self.client.defaults["HTTP_AUTHORIZATION"] = scheme.api_key
		resp = self.client.get(
			"/articles/", HTTP_ORIGIN=f"https://{self.pub_site.domain}"
		)
		self.assertEqual(resp.status_code, 200)
		titles = [a["title"] for a in resp.data["results"]]
		self.assertIn("Mine Article SR", titles)

	def test_signed_in_member_ignores_a_conflicting_origin(self):
		user = User.objects.create_user(username="sr-member", password="pw")
		OrganizationUser.objects.create(organization=self.my_org, user=user)
		self.client.force_authenticate(user=user)
		resp = self.client.get(
			"/articles/", HTTP_ORIGIN=f"https://{self.pub_site.domain}"
		)
		self.assertEqual(resp.status_code, 200)
		titles = [a["title"] for a in resp.data["results"]]
		self.assertIn("Mine Article SR", titles)


class NonApiSurfacesUnaffectedTest(TestCase):
	"""RSS feeds and sitemaps resolve by the REQUESTED SITE (Phase 4b/5),
	never by caller identity, so they never read visible_subject_ids and
	must keep working with no site/Origin indicator at all -- even amid
	the exact two-public-site ambiguity that gates the API."""

	def setUp(self):
		self.client = APIClient()
		self.org_a = Organization.objects.create(name="Surf Org A", slug="surf-org-a")
		self.org_b = Organization.objects.create(name="Surf Org B", slug="surf-org-b")
		self.team_a = Team.objects.create(
			organization=self.org_a, name="Surf Team A", slug="surf-team-a"
		)
		self.team_b = Team.objects.create(
			organization=self.org_b, name="Surf Team B", slug="surf-team-b"
		)
		self.subject_a = Subject.objects.create(
			subject_name="Surf Subject A", subject_slug="surf-subj-a", team=self.team_a
		)
		self.subject_b = Subject.objects.create(
			subject_name="Surf Subject B", subject_slug="surf-subj-b", team=self.team_b
		)
		self.site_a = publish_subjects(
			self.subject_a, organization=self.org_a, name="Surf Site A"
		)
		self.site_b = publish_subjects(
			self.subject_b, organization=self.org_b, name="Surf Site B"
		)
		self.custom_setting_a = CustomSetting.objects.get(site=self.site_a)
		self.custom_setting_a.rss_enabled = True
		self.custom_setting_a.generate_sitemap = True
		self.custom_setting_a.sitemap_subjects.add(self.subject_a)
		self.custom_setting_a.save(update_fields=["rss_enabled", "generate_sitemap"])

	def test_api_still_400s_amid_this_ambiguity(self):
		"""Sanity check that this fixture really does create the ambiguity
		the rest of this test asserts RSS/sitemaps are immune to."""
		resp = self.client.get("/articles/")
		self.assertEqual(resp.status_code, 400)

	def test_site_scoped_rss_feed_unaffected_by_caller_ambiguity(self):
		resp = self.client.get(
			f"/feed/sites/{self.site_a.pk}/trials/subject/{self.subject_a.subject_slug}/"
		)
		self.assertEqual(resp.status_code, 200)

	def test_old_rss_url_still_redirects_amid_the_ambiguity(self):
		resp = self.client.get(
			f"/feed/trials/subject/{self.subject_a.subject_slug}/"
		)
		self.assertEqual(resp.status_code, 301)

	def test_site_scoped_sitemap_unaffected_by_caller_ambiguity(self):
		resp = self.client.get(f"/sitemap/sites/{self.site_a.pk}/index.xml")
		self.assertEqual(resp.status_code, 200)
