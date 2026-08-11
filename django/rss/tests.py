"""
The author RSS feed's <link> element points at the site's author profile
page when CustomSetting.has_author_pages is on, and at orcid.org
otherwise. See docs/06-organisations-teams-and-sites.md#author-profile-page-links.
"""

from django.contrib.sites.models import Site
from django.test import TestCase
from django.urls import reverse

from gregory.models import Articles, Authors, OrganizationApiSettings, Subject, Team
from organizations.models import Organization
from sitesettings.models import CustomSetting


class AuthorFeedLinkTest(TestCase):
	def setUp(self):
		self.organization = Organization.objects.create(
			name="RSS Author Org", slug="rss-author-org"
		)
		OrganizationApiSettings.objects.filter(organization=self.organization).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="RSS Author Team",
			organization=self.organization,
			slug="rss-author-team",
		)
		self.subject = Subject.objects.create(
			subject_name="RSS Author Subject",
			team=self.team,
			subject_slug="rss-author-subject",
		)
		self.site = Site.objects.get_or_create(
			id=1, defaults={"domain": "testserver", "name": "Test Site"}
		)[0]
		if self.site.domain != "testserver":
			self.site.domain = "testserver"
			self.site.save()
		self.author = Authors.objects.create(
			given_name="Jane", family_name="Doe", ORCID="0000-0002-7922-9785"
		)
		article = Articles.objects.create(
			title="RSS Author Article",
			doi="10.9999/rss-author-1",
			link="https://example.com/rss-author-1",
		)
		article.subjects.add(self.subject)
		article.teams.add(self.team)
		article.authors.add(self.author)

	def _fetch_feed(self):
		url = reverse("articles_by_author_feed", args=[self.author.ORCID])
		response = self.client.get(url)
		self.assertEqual(response.status_code, 200)
		return response.content.decode()

	def test_link_points_to_site_when_flag_on(self):
		CustomSetting.objects.create(
			site=self.site, title="Flag On Site", has_author_pages=True
		)
		content = self._fetch_feed()
		self.assertIn(
			"<link>https://testserver/authors/0000-0002-7922-9785/</link>", content
		)

	def test_link_points_to_orcid_when_flag_off(self):
		CustomSetting.objects.create(
			site=self.site, title="Flag Off Site", has_author_pages=False
		)
		content = self._fetch_feed()
		self.assertIn(
			"<link>https://orcid.org/0000-0002-7922-9785</link>", content
		)

	def test_link_points_to_orcid_when_no_custom_setting(self):
		content = self._fetch_feed()
		self.assertIn(
			"<link>https://orcid.org/0000-0002-7922-9785</link>", content
		)
