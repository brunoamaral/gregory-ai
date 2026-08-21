"""
HTTP-level tests for the author outreach opt-out endpoint —
``/subscriptions/author-optout/<uuid:token>/``. See docs/author-outreach.md and docs/author-outreach-spec.md "Legal basis and
consent" ("Opt-out"): GET renders a confirmation page and must not mutate
anything — mail clients and security scanners prefetch links, and a
prefetching GET would silently opt someone out; the actual opt-out only
happens on POST, and is idempotent.

The opt-out affects future email only. It must never hide, alter, or
unpublish the author's profile page — AuthorOptOutDoesNotAffectProfilePageTest
asserts that directly against the live Authors API, the same data source the
public profile page reads from.
"""

from django.contrib.sites.models import Site
from django.test import Client, TestCase
from rest_framework.test import APIClient

from gregory.models import Articles, Authors, OrganizationApiSettings, Team
from organizations.models import Organization
from subscriptions.models import (
	AuthorContactOptOut,
	AuthorOutreach,
	AuthorOutreachCampaign,
)


class AuthorOptOutViewTest(TestCase):
	def setUp(self):
		self.client = Client()
		self.site = Site.objects.create(
			domain="optout-view.example.com", name="Opt-Out View"
		)
		self.campaign = AuthorOutreachCampaign.objects.create(
			site=self.site, name="View Campaign", utm_campaign_slug="view-campaign"
		)
		self.author = Authors.objects.create(
			given_name="Ada",
			family_name="Researcher",
			ORCID="0000-0000-0000-8001",
			emails=["ada-view@example.com"],
			orcid_claimed=True,
			orcid_verified_email=True,
		)
		self.outreach = AuthorOutreach.objects.create(
			campaign=self.campaign,
			site=self.site,
			author=self.author,
			email="ada-view@example.com",
		)
		self.url = f"/subscriptions/author-optout/{self.outreach.opt_out_token}/"

	def test_token_resolves_and_renders_confirmation(self):
		response = self.client.get(self.url)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "ada-view@example.com")

	def test_get_does_not_mutate(self):
		self.client.get(self.url)
		self.assertEqual(AuthorContactOptOut.objects.count(), 0)
		self.outreach.refresh_from_db()
		self.assertEqual(self.outreach.status, AuthorOutreach.STATUS_PENDING)

	def test_unknown_token_404s_on_get_and_post(self):
		bogus_url = (
			"/subscriptions/author-optout/00000000-0000-0000-0000-000000000000/"
		)
		self.assertEqual(self.client.get(bogus_url).status_code, 404)
		self.assertEqual(self.client.post(bogus_url).status_code, 404)

	def test_post_creates_opt_out_row(self):
		response = self.client.post(self.url)
		self.assertEqual(response.status_code, 200)
		row = AuthorContactOptOut.objects.get(email="ada-view@example.com")
		self.assertEqual(row.reason, AuthorContactOptOut.REASON_OPT_OUT)
		self.assertEqual(row.author_id, self.author.author_id)

	def test_post_is_idempotent(self):
		first = self.client.post(self.url)
		second = self.client.post(self.url)
		self.assertEqual(first.status_code, 200)
		self.assertEqual(second.status_code, 200)
		self.assertEqual(
			AuthorContactOptOut.objects.filter(email="ada-view@example.com").count(), 1
		)

	def test_post_does_not_change_outreach_row_status(self):
		# The opt-out affects future email only; it is not a way to cancel
		# a still-pending queue row (that's the admin's Skip action).
		self.client.post(self.url)
		self.outreach.refresh_from_db()
		self.assertEqual(self.outreach.status, AuthorOutreach.STATUS_PENDING)


class AuthorOptOutDoesNotAffectProfilePageTest(TestCase):
	"""
	docs/author-outreach-spec.md "Legal basis and consent": "Opt-out scope:
	Future email only. It does not hide, alter, or unpublish the author
	profile page." Asserted directly against the live Authors API — the
	same data source the public author profile page reads from.
	"""

	def setUp(self):
		self.organization = Organization.objects.create(name="Profile Org")
		OrganizationApiSettings.objects.filter(organization=self.organization).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			organization=self.organization, name="Profile Team", slug="profile-team"
		)
		self.site = Site.objects.create(
			domain="optout-profile.example.com", name="Profile"
		)
		self.campaign = AuthorOutreachCampaign.objects.create(
			site=self.site, name="Profile Campaign", utm_campaign_slug="profile-campaign"
		)
		self.author = Authors.objects.create(
			given_name="Ada",
			family_name="Profile",
			ORCID="0000-0000-0000-8002",
			emails=["ada-profile@example.com"],
			orcid_claimed=True,
			orcid_verified_email=True,
		)
		self.article = Articles.objects.create(
			title="Profile visibility article",
			link="http://example.com/profile-visibility-article",
		)
		self.article.authors.add(self.author)
		self.article.teams.add(self.team)
		self.outreach = AuthorOutreach.objects.create(
			campaign=self.campaign,
			site=self.site,
			author=self.author,
			email="ada-profile@example.com",
		)
		self.api_client = APIClient()
		self.django_client = Client()
		self.profile_url = f"/authors/{self.author.author_id}/"
		self.optout_url = f"/subscriptions/author-optout/{self.outreach.opt_out_token}/"

	def test_profile_page_unchanged_after_opt_out(self):
		before = self.api_client.get(self.profile_url)
		self.assertEqual(before.status_code, 200)

		response = self.django_client.post(self.optout_url)
		self.assertEqual(response.status_code, 200)
		self.assertTrue(
			AuthorContactOptOut.objects.filter(email="ada-profile@example.com").exists()
		)

		after = self.api_client.get(self.profile_url)
		self.assertEqual(after.status_code, 200)
		self.assertEqual(before.data, after.data)

		# The opt-out write never touches Authors itself.
		self.author.refresh_from_db()
		self.assertEqual(self.author.full_name, "Ada Profile")
		self.assertTrue(self.author.orcid_claimed)
