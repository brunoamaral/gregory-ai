"""
The author RSS feed's <link> element points at the site's author profile
page when CustomSetting.has_author_pages is on, and at orcid.org
otherwise. See docs/06-organisations-teams-and-sites.md#author-profile-page-links.
"""

from django.contrib.sites.models import Site
from django.test import TestCase
from django.urls import reverse

from gregory.models import (
	Articles,
	Authors,
	OrganizationApiSettings,
	Subject,
	Team,
	Trials,
)
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
		# What makes the feed's content visible to an anonymous caller is a
		# subject in some api_public site's scope -- not the org flag, and
		# not this site's own CustomSetting. It lives on a separate site so
		# that each test below is still free to create, vary or omit the
		# CustomSetting for `self.site`, which is the only thing these tests
		# are actually about (has_author_pages -> <link> target).
		self.scope_site = Site.objects.create(
			domain="scope.example.com", name="Scope Site"
		)
		scope_settings = CustomSetting.objects.create(
			site=self.scope_site, title="Scope settings", api_public=True
		)
		scope_settings.scope_subjects.add(self.subject)
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


class FeedVisibilityTest(TestCase):
	"""
	Both RSS feeds are scoped by subject, not by the owning team's
	organisation (site-scoped API visibility, Phase 4). A subject reaches an
	anonymous caller only by sitting in the scope_subjects of some site with
	api_public on; nothing else grants access, and team ownership grants
	none.
	"""

	def setUp(self):
		self.org = Organization.objects.create(name="Feed Org", slug="feed-org")
		OrganizationApiSettings.objects.filter(organization=self.org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Feed Team", organization=self.org, slug="feed-team"
		)
		self.published = Subject.objects.create(
			subject_name="Published", subject_slug="published", team=self.team
		)
		# Owned by the same public organisation's team, but never curated
		# into a site's scope. Under the old organisation rule this was
		# visible; it is the narrowing this conversion introduces.
		self.uncurated = Subject.objects.create(
			subject_name="Uncurated", subject_slug="uncurated", team=self.team
		)
		# No team at all. The old check skipped subjects like this entirely
		# ("if subject.team_id is not None"), so they were served to anyone
		# who guessed the slug -- the hole this conversion closes.
		self.teamless = Subject.objects.create(
			subject_name="Teamless", subject_slug="teamless", team=None
		)

		self.site = Site.objects.create(domain="feeds.example.com", name="Feeds")
		settings_row = CustomSetting.objects.create(
			site=self.site, title="Feed settings", api_public=True
		)
		settings_row.scope_subjects.add(self.published)

		for subject in (self.published, self.uncurated, self.teamless):
			trial = Trials.objects.create(
				title=f"trial-{subject.subject_slug}",
				link=f"https://registry.example.org/{subject.subject_slug}",
			)
			trial.teams.add(self.team)
			trial.subjects.add(subject)

	def _trials_feed(self, subject):
		return self.client.get(
			reverse("trials_by_subject_feed", args=[subject.subject_slug])
		)

	def test_subject_in_a_public_sites_scope_is_served(self):
		response = self._trials_feed(self.published)
		self.assertEqual(response.status_code, 200)
		self.assertIn("trial-published", response.content.decode())

	def test_subject_owned_by_a_public_org_but_uncurated_404s(self):
		self.assertEqual(self._trials_feed(self.uncurated).status_code, 404)

	def test_subject_with_no_team_404s(self):
		self.assertEqual(self._trials_feed(self.teamless).status_code, 404)

	def test_subject_with_no_team_is_served_once_a_site_curates_it(self):
		# The counterpart to the test above, and the reason it passes: a
		# team-less subject is unreachable because nothing publishes it, not
		# because it has no team. Curation is the whole grant, so an
		# administrator who puts one in a public site's scope has published
		# it deliberately and it resolves like any other subject. Pinned so
		# that a well-meaning "team-less subjects are never public" guard
		# cannot be added back without failing here.
		settings_row = CustomSetting.objects.get(site=self.site)
		settings_row.scope_subjects.add(self.teamless)
		response = self._trials_feed(self.teamless)
		self.assertEqual(response.status_code, 200)
		self.assertIn("trial-teamless", response.content.decode())

	def test_author_feed_404s_when_no_article_carries_a_visible_subject(self):
		author = Authors.objects.create(
			given_name="Un", family_name="Seen", ORCID="0000-0002-0000-0009"
		)
		article = Articles.objects.create(
			title="uncurated-article", link="https://example.org/uncurated"
		)
		article.teams.add(self.team)
		article.subjects.add(self.uncurated)
		article.authors.add(author)
		self.assertEqual(
			self.client.get(
				reverse("articles_by_author_feed", args=[author.ORCID])
			).status_code,
			404,
		)

	def test_author_feed_lists_only_visible_subject_articles(self):
		author = Authors.objects.create(
			given_name="Mixed", family_name="Bag", ORCID="0000-0002-0000-0010"
		)
		for subject, slug in ((self.published, "shown"), (self.uncurated, "hidden")):
			article = Articles.objects.create(
				title=f"{slug}-article", link=f"https://example.org/{slug}"
			)
			article.teams.add(self.team)
			article.subjects.add(subject)
			article.authors.add(author)
		body = self.client.get(
			reverse("articles_by_author_feed", args=[author.ORCID])
		).content.decode()
		self.assertIn("shown-article", body)
		self.assertNotIn("hidden-article", body)
