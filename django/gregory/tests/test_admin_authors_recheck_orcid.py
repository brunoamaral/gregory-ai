"""
Tests for the Authors admin's "Recheck ORCID now" button/view
(gregory.admin.AuthorsAdmin.recheck_orcid_view).

Run with:
    docker exec gregory python manage.py test gregory.tests.test_admin_authors_recheck_orcid
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from organizations.models import Organization

from gregory.models import Articles, Authors, Team

User = get_user_model()


class AuthorsRecheckOrcidViewTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Org", slug="recheck-org")
		self.team = Team.objects.create(
			organization=self.org, name="Team", slug="recheck-team"
		)
		self.author = Authors.objects.create(
			given_name="Ada", family_name="Lovelace", ORCID="0000-0001-2345-6789"
		)
		self.article = Articles.objects.create(
			title="Article", link="https://example.com/a"
		)
		self.article.teams.add(self.team)
		self.article.authors.add(self.author)

		self.superuser = User.objects.create_superuser(
			username="admin", email="admin@example.com", password="pw"
		)
		self.url = reverse("admin:gregory_authors_recheck_orcid", args=[self.author.pk])
		self.change_url = reverse(
			"admin:gregory_authors_change", args=[self.author.pk]
		)

	def test_get_renders_confirmation_without_mutating(self):
		self.client.force_login(self.superuser)

		response = self.client.get(self.url)

		self.assertEqual(response.status_code, 200)
		self.author.refresh_from_db()
		self.assertIsNone(self.author.orcid_check)

	@patch(
		"subscriptions.management.commands.utils.get_credentials.get_orcid_credentials",
		return_value=("id", "secret"),
	)
	@patch("orcid.PublicAPI")
	def test_post_refreshes_author_from_orcid(self, mock_public_api, mock_get_creds):
		self.client.force_login(self.superuser)
		instance = MagicMock()
		instance.get_search_token_from_orcid.return_value = "tok"
		instance.read_record_public.return_value = {
			"person": {
				"addresses": {"address": [{"country": {"value": "GB"}}]},
				"biography": {"content": "Bio text."},
			}
		}
		mock_public_api.return_value = instance

		response = self.client.post(self.url)

		self.assertRedirects(response, self.change_url)
		self.author.refresh_from_db()
		self.assertEqual(self.author.country, "GB")
		self.assertEqual(self.author.biography, "Bio text.")

	def test_anonymous_user_cannot_trigger_recheck(self):
		response = self.client.post(self.url)

		self.assertNotEqual(response.status_code, 200)
		self.author.refresh_from_db()
		self.assertIsNone(self.author.orcid_check)

	def test_staff_without_change_permission_is_forbidden(self):
		staff = User.objects.create_user(
			username="staff-no-perm", password="pw", is_staff=True
		)
		self.client.force_login(staff)

		response = self.client.post(self.url)

		self.assertEqual(response.status_code, 403)
		self.author.refresh_from_db()
		self.assertIsNone(self.author.orcid_check)
