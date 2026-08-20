"""
Regression tests for HOUSE-LOAD-SPIKE-P2-QUERY-COST.md item 2
(OrgVisibilityMixin's Exists() rewrite from a self-join to a direct
through-table traversal with team ids resolved in Python).

These two cases were the ones the plan flagged as untested anywhere in the
suite before this rewrite:
  - an article whose *team* is soft-deleted (Team.is_active=False) must stay
    visible — the mixin resolves organisation ids to team ids via
    Team.all_objects, not the ActiveTeamManager default, to preserve the
    pre-rewrite behaviour (the raw SQL it replaced joined gregory_team with
    no is_active filter at all).
  - an article with no teams at all must stay invisible (fail-closed), same
    as before the rewrite.

Run with:
    docker exec gregory python manage.py test api.tests.test_visibility_org_filter_edge_cases
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from organizations.models import Organization, OrganizationUser
from rest_framework.test import APIClient

from gregory.models import Articles, OrganizationApiSettings, Team, Trials

User = get_user_model()


def _make_org(name, slug, public=False):
	org = Organization.objects.create(name=name, slug=slug)
	OrganizationApiSettings.objects.filter(organization=org).update(
		make_api_public=public
	)
	return org


class OrgVisibilityThroughTableEdgeCaseTest(TestCase):
	def setUp(self):
		self.org = _make_org("Edge Org", "edge-org", public=False)
		self.user = User.objects.create_user(username="member", password="pw")
		OrganizationUser.objects.create(organization=self.org, user=self.user)

		self.client = APIClient()
		self.client.force_login(self.user)

	def test_article_with_inactive_team_stays_visible(self):
		team = Team.objects.create(
			organization=self.org,
			name="Soft-deleted Team",
			slug="soft-deleted-team",
			is_active=False,
		)
		# Team.objects (ActiveTeamManager) filters is_active=True and would
		# not return this team; confirm the fixture is set up as intended.
		self.assertFalse(Team.objects.filter(pk=team.pk).exists())
		self.assertTrue(Team.all_objects.filter(pk=team.pk).exists())

		article = Articles.objects.create(
			title="Inactive Team Article", link="https://ex.com/inactive-team"
		)
		article.teams.add(team)

		resp = self.client.get("/articles/")
		self.assertEqual(resp.status_code, 200)
		ids = [a["article_id"] for a in resp.data["results"]]
		self.assertIn(article.article_id, ids)

	def test_trial_with_inactive_team_stays_visible(self):
		team = Team.objects.create(
			organization=self.org,
			name="Soft-deleted Trial Team",
			slug="soft-deleted-trial-team",
			is_active=False,
		)
		trial = Trials.objects.create(
			title="Inactive Team Trial", link="https://ex.com/inactive-trial"
		)
		trial.teams.add(team)

		resp = self.client.get("/trials/")
		self.assertEqual(resp.status_code, 200)
		ids = [t["trial_id"] for t in resp.data["results"]]
		self.assertIn(trial.trial_id, ids)

	def test_team_less_article_stays_invisible(self):
		article = Articles.objects.create(
			title="No Team Article", link="https://ex.com/no-team"
		)
		resp = self.client.get("/articles/")
		self.assertEqual(resp.status_code, 200)
		ids = [a["article_id"] for a in resp.data["results"]]
		self.assertNotIn(article.article_id, ids)

	def test_team_less_trial_stays_invisible(self):
		trial = Trials.objects.create(
			title="No Team Trial", link="https://ex.com/no-team-trial"
		)
		resp = self.client.get("/trials/")
		self.assertEqual(resp.status_code, 200)
		ids = [t["trial_id"] for t in resp.data["results"]]
		self.assertNotIn(trial.trial_id, ids)
