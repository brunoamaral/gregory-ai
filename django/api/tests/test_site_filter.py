import csv
import io

from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from gregory.models import (
	Articles,
	OrganizationApiSettings,
	Sources,
	Team,
	Trials,
)
from organizations.models import Organization


class SiteFilterTest(TestCase):
	"""``site_id`` filters Articles/Trials to any team attached to that
	Django Site, deduplicating when an object belongs to multiple teams
	sharing the same site, and respecting org visibility."""

	def setUp(self):
		self.site_a = Site.objects.create(domain="site-a.example.com", name="Site A")
		self.site_b = Site.objects.create(domain="site-b.example.com", name="Site B")

		self.user = User.objects.create_user(username="sitetest", password="12345")
		self.public_org = Organization.objects.create(
			name="Public Org", slug="public-org"
		)
		self.public_org.add_user(self.user)
		OrganizationApiSettings.objects.update_or_create(
			organization=self.public_org, defaults={"make_api_public": True}
		)

		self.private_org = Organization.objects.create(
			name="Private Org", slug="private-org"
		)

		self.source = Sources.objects.create(
			name="Site Filter Source", source_for="science paper"
		)

		# Two teams on the public org, both attached to site_a.
		self.team1 = Team.objects.create(
			organization=self.public_org, name="Team One", slug="team-one", site=self.site_a
		)
		self.team2 = Team.objects.create(
			organization=self.public_org, name="Team Two", slug="team-two", site=self.site_a
		)
		# A private-org team also on site_a.
		self.private_team = Team.objects.create(
			organization=self.private_org,
			name="Private Team",
			slug="private-team",
			site=self.site_a,
		)
		# A team on site_b, for the "no teams on this site" check.
		self.team_other_site = Team.objects.create(
			organization=self.public_org,
			name="Other Site Team",
			slug="other-site-team",
			site=self.site_b,
		)

		# Article belonging to both team1 and team2 (same site) -- must not duplicate.
		self.shared_article = Articles.objects.create(
			title="Shared Article",
			summary="Shared summary",
			link="https://example.com/shared-article",
			kind="science paper",
		)
		self.shared_article.sources.add(self.source)
		self.shared_article.teams.add(self.team1, self.team2)

		# Article only on the private team's site_a team.
		self.private_article = Articles.objects.create(
			title="Private Article",
			summary="Private summary",
			link="https://example.com/private-article",
			kind="science paper",
		)
		self.private_article.sources.add(self.source)
		self.private_article.teams.add(self.private_team)

		# Trial mirrors the same shape.
		self.shared_trial = Trials.objects.create(title="Shared Trial")
		self.shared_trial.teams.add(self.team1, self.team2)

		self.client = APIClient()
		self.client.force_authenticate(user=self.user)
		self.anon_client = APIClient()

	def test_article_shared_across_teams_on_same_site_not_duplicated(self):
		response = self.client.get(
			reverse("articles-list"), {"site_id": self.site_a.id, "page_size": 50}
		)
		ids = [item["article_id"] for item in response.data["results"]]
		self.assertEqual(ids.count(self.shared_article.article_id), 1)

	def test_site_with_no_teams_returns_empty(self):
		empty_site = Site.objects.create(domain="empty.example.com", name="Empty")
		response = self.client.get(
			reverse("articles-list"), {"site_id": empty_site.id}
		)
		self.assertEqual(response.data["count"], 0)

	def test_anonymous_caller_excludes_private_org_team_on_shared_site(self):
		response = self.anon_client.get(
			reverse("articles-list"), {"site_id": self.site_a.id, "page_size": 50}
		)
		ids = {item["article_id"] for item in response.data["results"]}
		self.assertIn(self.shared_article.article_id, ids)
		self.assertNotIn(self.private_article.article_id, ids)

	def test_site_id_csv_all_results_row_count_matches_orm_count(self):
		# self.user is only a member of public_org, so org visibility scopes
		# the count to public_org's teams on site_a (team1, team2) --
		# private_team's article on the same site must not be counted.
		expected_count = (
			Articles.objects.filter(
				teams__site_id=self.site_a.id, teams__organization_id=self.public_org.id
			)
			.distinct()
			.count()
		)
		response = self.client.get(
			reverse("articles-list"),
			{"site_id": self.site_a.id, "format": "csv", "all_results": "true"},
		)
		content = b"".join(response.streaming_content).decode("utf-8")
		rows = list(csv.reader(io.StringIO(content)))
		self.assertEqual(len(rows) - 1, expected_count)

	def test_trial_shared_across_teams_on_same_site_not_duplicated(self):
		response = self.client.get(
			reverse("trials-list"), {"site_id": self.site_a.id, "page_size": 50}
		)
		ids = [item["trial_id"] for item in response.data["results"]]
		self.assertEqual(ids.count(self.shared_trial.trial_id), 1)
