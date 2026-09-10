"""
Edge cases for SubjectVisibilityMixin's through-table traversal.

Descended from regression tests for HOUSE-LOAD-SPIKE-P2-QUERY-COST.md item 2,
which pinned OrgVisibilityMixin's Exists() rewrite. Phase 4 of site-scoped
API visibility replaced that mixin, and the traversal is now over the
`subjects` M2M rather than `teams`, so what these cases pin has shifted:

  - The team-soft-delete case is no longer about resolving team ids through
    Team.all_objects (there is nothing to resolve -- subject ids are what the
    through table holds). It is kept, restated, because it now asserts
    something stronger and worth keeping: team state cannot affect visibility
    AT ALL, in either direction.
  - The fail-closed case survives intact and matters more than before: a row
    with no subjects is invisible to everyone. That is the whole reason
    subject-less content is slated for pruning rather than left to sit.

Run with:
    docker exec gregory python manage.py test api.tests.test_visibility_org_filter_edge_cases
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from organizations.models import Organization, OrganizationUser
from rest_framework.test import APIClient

from api.tests.visibility_helpers import publish_subjects
from gregory.models import (
	Articles,
	OrganizationApiSettings,
	Subject,
	Team,
	Trials,
)

User = get_user_model()


def _make_org(name, slug, public=False):
	org = Organization.objects.create(name=name, slug=slug)
	OrganizationApiSettings.objects.filter(organization=org).update(
		make_api_public=public
	)
	return org


class SubjectVisibilityThroughTableEdgeCaseTest(TestCase):
	def setUp(self):
		self.org = _make_org("Edge Org", "edge-org", public=False)
		self.user = User.objects.create_user(username="member", password="pw")
		OrganizationUser.objects.create(organization=self.org, user=self.user)

		self.owning_team = Team.objects.create(
			organization=self.org, name="Edge Team", slug="edge-team"
		)
		self.subject = Subject.objects.create(
			subject_name="Edge Subject",
			subject_slug="edge-subject",
			team=self.owning_team,
		)
		publish_subjects(self.subject, organization=self.org)

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
		article.subjects.add(self.subject)

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
		trial.subjects.add(self.subject)

		resp = self.client.get("/trials/")
		self.assertEqual(resp.status_code, 200)
		ids = [t["trial_id"] for t in resp.data["results"]]
		self.assertIn(trial.trial_id, ids)

	def test_subject_less_article_stays_invisible(self):
		article = Articles.objects.create(
			title="No Subject Article", link="https://ex.com/no-subject"
		)
		# Deliberately given a team the caller CAN reach, so the assertion
		# below can only be explained by the missing subject.
		article.teams.add(self.owning_team)
		resp = self.client.get("/articles/")
		self.assertEqual(resp.status_code, 200)
		ids = [a["article_id"] for a in resp.data["results"]]
		self.assertNotIn(article.article_id, ids)

	def test_subject_less_trial_stays_invisible(self):
		trial = Trials.objects.create(
			title="No Subject Trial", link="https://ex.com/no-subject-trial"
		)
		trial.teams.add(self.owning_team)
		resp = self.client.get("/trials/")
		self.assertEqual(resp.status_code, 200)
		ids = [t["trial_id"] for t in resp.data["results"]]
		self.assertNotIn(trial.trial_id, ids)
