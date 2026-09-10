"""
Tests for team listing on /teams/.

A Team is not content: it carries no subject of its own, so subject scope --
the rule everywhere else since Phase 4 of site-scoped API visibility -- has
nothing to say about it. `/teams/` is gated by the explicit `Team.api_listed`
flag instead, seeded by `sitesettings/0019` from the teams that owned a
subject in a public scope and overridable in either direction after that.

That makes this endpoint **caller-independent**, which is the whole change
this suite records. The previous rule was "teams in organisations you can
see", so the same request returned different teams to an anonymous caller, a
member and an API key, and `?include_public=true` widened it. Now every
caller gets the same list and the flag is the only input.

The flag gates *listing*, not *access*: `api_listed = False` removes a team
from this endpoint and does nothing to the visibility of that team's
articles, trials or subjects, which subject scope governs. Keeping those two
separate is deliberate -- conflating them is how a metadata switch quietly
becomes a data-visibility switch.

Run with:
    docker exec gregory python manage.py test api.tests.test_visibility_teams
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.timezone import now
from organizations.models import Organization, OrganizationUser
from rest_framework.test import APIClient

from api.models import APIAccessScheme
from gregory.models import OrganizationApiSettings, Team

User = get_user_model()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_org(name, slug, public=False):
	org = Organization.objects.create(name=name, slug=slug)
	OrganizationApiSettings.objects.filter(organization=org).update(
		make_api_public=public
	)
	return org


def _make_team(org, name, api_listed=False):
	slug = name.lower().replace(" ", "-")
	return Team.objects.create(
		organization=org, name=name, slug=slug, api_listed=api_listed
	)


def _make_api_scheme(org, name):
	return APIAccessScheme.objects.create(
		client_name=name,
		client_contacts=f"{name}@example.com",
		organization=org,
		ip_addresses="",
		begin_date=now() - timedelta(days=1),
		end_date=now() + timedelta(days=30),
	)


# ---------------------------------------------------------------------------
# Base setUp
# ---------------------------------------------------------------------------


class TeamListingBase(TestCase):
	def setUp(self):
		self.my_org = _make_org("My Org", "my-org-team", public=False)
		self.pub_org = _make_org("Public Org", "pub-org-team", public=True)
		self.priv_org = _make_org("Private Org", "priv-org-team", public=False)

		# api_listed cuts across the public/private organisation split on
		# purpose: the flag is independent of who owns the team, which is
		# exactly what the endpoint no longer derives.
		self.listed_team = _make_team(self.pub_org, "Listed Team", api_listed=True)
		self.unlisted_public_team = _make_team(
			self.pub_org, "Unlisted Public Team", api_listed=False
		)
		self.my_team = _make_team(self.my_org, "My Team Teams", api_listed=False)
		self.my_listed_team = _make_team(
			self.my_org, "My Listed Team", api_listed=True
		)
		self.priv_team = _make_team(self.priv_org, "Priv Team Teams", api_listed=False)

		self.client = APIClient()

	def _listed_ids(self):
		resp = self.client.get("/teams/")
		self.assertEqual(resp.status_code, 200)
		return [t["id"] for t in resp.data["results"]]


# ---------------------------------------------------------------------------
# Anonymous caller
# ---------------------------------------------------------------------------


class AnonymousTeamListingTest(TeamListingBase):
	def test_list_does_not_require_auth(self):
		self.assertEqual(self.client.get("/teams/").status_code, 200)

	def test_list_includes_listed_team(self):
		self.assertIn(self.listed_team.id, self._listed_ids())

	def test_list_excludes_unlisted_teams(self):
		ids = self._listed_ids()
		self.assertNotIn(self.unlisted_public_team.id, ids)
		self.assertNotIn(self.my_team.id, ids)
		self.assertNotIn(self.priv_team.id, ids)

	def test_a_public_organisation_does_not_make_its_team_listed(self):
		# The old rule listed every team of a public organisation. The flag
		# is now the only input, so an unlisted team in a public
		# organisation stays out.
		self.assertNotIn(self.unlisted_public_team.id, self._listed_ids())

	def test_detail_unlisted_returns_404(self):
		resp = self.client.get(f"/teams/{self.unlisted_public_team.id}/")
		self.assertEqual(resp.status_code, 404)

	def test_detail_listed_returns_200(self):
		resp = self.client.get(f"/teams/{self.listed_team.id}/")
		self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# The listing is the same for every caller
# ---------------------------------------------------------------------------


class AuthenticatedUserTeamListingTest(TeamListingBase):
	def setUp(self):
		super().setUp()
		self.user = User.objects.create_user(username="team-member", password="pw")
		OrganizationUser.objects.create(organization=self.my_org, user=self.user)
		self.client.force_login(self.user)

	def test_member_sees_the_same_listing_as_anonymous(self):
		ids = self._listed_ids()
		self.assertIn(self.listed_team.id, ids)
		self.assertIn(self.my_listed_team.id, ids)
		self.assertNotIn(self.unlisted_public_team.id, ids)
		self.assertNotIn(self.priv_team.id, ids)

	def test_membership_does_not_reveal_an_unlisted_team(self):
		# Worth stating explicitly: the user belongs to my_org, and my_team
		# is my_org's, but the flag governs and it is off. Under the old
		# organisation rule this team WAS visible to this caller.
		ids = self._listed_ids()
		self.assertNotIn(self.my_team.id, ids)
		self.assertEqual(
			self.client.get(f"/teams/{self.my_team.id}/").status_code, 404
		)

	def test_include_public_is_a_no_op(self):
		# The flag is caller-independent, so there is no "own scope" for the
		# flag to widen. The parameter is accepted and changes nothing.
		plain = self._listed_ids()
		resp = self.client.get("/teams/?include_public=true")
		self.assertEqual(resp.status_code, 200)
		self.assertEqual([t["id"] for t in resp.data["results"]], plain)

	def test_detail_own_listed_returns_200(self):
		resp = self.client.get(f"/teams/{self.my_listed_team.id}/")
		self.assertEqual(resp.status_code, 200)


class APIKeyTeamListingTest(TeamListingBase):
	def setUp(self):
		super().setUp()
		self.scheme = _make_api_scheme(self.my_org, "team-key")
		self.client.credentials(HTTP_AUTHORIZATION=self.scheme.api_key)

	def test_key_sees_the_same_listing_as_anonymous(self):
		ids = self._listed_ids()
		self.assertIn(self.listed_team.id, ids)
		self.assertIn(self.my_listed_team.id, ids)
		self.assertNotIn(self.unlisted_public_team.id, ids)
		self.assertNotIn(self.my_team.id, ids)
		self.assertNotIn(self.priv_team.id, ids)

	def test_detail_unlisted_returns_404(self):
		resp = self.client.get(f"/teams/{self.my_team.id}/")
		self.assertEqual(resp.status_code, 404)
