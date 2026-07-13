"""
Regression test: TeamSerializer must not leak `members` (Django auth User
IDs of everyone on the team) or other internal fields via `fields = "__all__"`.

Run with:
    docker exec gregory python manage.py test api.tests.test_team_serializer_fields
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from organizations.models import Organization
from rest_framework.test import APIClient

from gregory.models import OrganizationApiSettings, Team

User = get_user_model()


class TeamSerializerFieldWhitelistTests(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(
			name="Team Fields Org", slug="team-fields-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			organization=self.org, name="Team Fields Team", slug="team-fields-team"
		)
		self.user = User.objects.create_user(
			username="team-fields-member", password="unused"
		)
		self.team.members.add(self.user)
		self.client = APIClient()

	def test_members_not_in_list_response(self):
		resp = self.client.get("/teams/")
		self.assertEqual(resp.status_code, 200)
		payload = resp.data["results"][0]
		self.assertNotIn("members", payload)

	def test_members_not_in_detail_response(self):
		resp = self.client.get(f"/teams/{self.team.id}/")
		self.assertEqual(resp.status_code, 200)
		self.assertNotIn("members", resp.data)

	def test_expected_public_fields_present(self):
		resp = self.client.get(f"/teams/{self.team.id}/")
		self.assertEqual(resp.status_code, 200)
		for field in ("id", "name", "slug", "organization", "is_active"):
			self.assertIn(field, resp.data)
