"""
Covers:
  - The old unprefixed feed URLs (feed/author/<orcid>/,
    feed/trials/subject/<slug>/) permanently redirect (301) onto the
    site-scoped equivalents, preserving any query string.
  - The author feed's <link> element points at the requested site's author
    profile page when its CustomSetting.has_author_pages is on, and at
    orcid.org otherwise. See
    docs/06-organisations-teams-and-sites.md#author-profile-page-links.
  - Site-scoped feed visibility: a feed is scoped to the REQUESTED site's
    scope_subjects, not team ownership and not caller identity (Phase 5 of
    site-scoped API visibility -- see docs/03-api-and-rss-feeds.md#rss-feeds).
    Caller-identity-scoped behaviour for RSS lived here before Phase 5; it
    now belongs to sitemaps'-style site-scoping instead, so those cases
    moved to this file too -- api/tests/test_visibility_rss.py is for the
    isolation/invariance surface that touches API-visibility fixtures
    (organisations, API keys).
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


class OldFeedUrlRedirectTest(TestCase):
	"""The load-bearing requirement of Phase 5: old URLs must 301, not 404."""

	def setUp(self):
		self.author = Authors.objects.create(
			given_name="Jane", family_name="Doe", ORCID="0000-0002-7922-9785"
		)

	def test_author_feed_redirects_permanently_to_site_3(self):
		response = self.client.get(
			reverse("articles_by_author_feed", args=[self.author.ORCID])
		)
		self.assertEqual(response.status_code, 301)
		self.assertEqual(
			response.headers["Location"],
			f"/feed/sites/3/author/{self.author.ORCID}/",
		)

	def test_trials_feed_redirects_permanently_to_site_3(self):
		response = self.client.get(
			reverse("trials_by_subject_feed", args=["some-subject"])
		)
		self.assertEqual(response.status_code, 301)
		self.assertEqual(
			response.headers["Location"], "/feed/sites/3/trials/subject/some-subject/"
		)

	def test_author_feed_redirect_preserves_query_string(self):
		response = self.client.get(
			reverse("articles_by_author_feed", args=[self.author.ORCID])
			+ "?include_public=true"
		)
		self.assertEqual(response.status_code, 301)
		self.assertEqual(
			response.headers["Location"],
			f"/feed/sites/3/author/{self.author.ORCID}/?include_public=true",
		)

	def test_trials_feed_redirect_preserves_query_string(self):
		response = self.client.get(
			reverse("trials_by_subject_feed", args=["some-subject"]) + "?p=2"
		)
		self.assertEqual(response.status_code, 301)
		self.assertEqual(
			response.headers["Location"],
			"/feed/sites/3/trials/subject/some-subject/?p=2",
		)

	def test_redirect_does_not_validate_the_orcid_or_slug(self):
		# A redirect is issued whatever the path segment is; the new URL is
		# what 404s for a bad orcid/slug, exactly as it would have here.
		response = self.client.get(
			reverse("articles_by_author_feed", args=["not-a-real-orcid"])
		)
		self.assertEqual(response.status_code, 301)


class SiteAuthorFeedLinkTest(TestCase):
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
		self.site = Site.objects.create(
			domain="link-test.example.com", name="Link Test Site"
		)
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
		url = reverse(
			"site_articles_by_author_feed",
			kwargs={"site_id": self.site.pk, "orcid": self.author.ORCID},
		)
		response = self.client.get(url)
		self.assertEqual(response.status_code, 200)
		return response.content.decode()

	def test_link_points_to_site_when_flag_on(self):
		CustomSetting.objects.create(
			site=self.site,
			title="Flag On Site",
			rss_enabled=True,
			has_author_pages=True,
		).scope_subjects.add(self.subject)
		content = self._fetch_feed()
		self.assertIn(
			f"<link>https://{self.site.domain}/authors/0000-0002-7922-9785/</link>",
			content,
		)

	def test_link_points_to_orcid_when_flag_off(self):
		CustomSetting.objects.create(
			site=self.site,
			title="Flag Off Site",
			rss_enabled=True,
			has_author_pages=False,
		).scope_subjects.add(self.subject)
		content = self._fetch_feed()
		self.assertIn(
			"<link>https://orcid.org/0000-0002-7922-9785</link>", content
		)

	def test_item_link_uses_the_requested_sites_domain(self):
		CustomSetting.objects.create(
			site=self.site, title="Item Link Site", rss_enabled=True
		).scope_subjects.add(self.subject)
		content = self._fetch_feed()
		self.assertIn(f"https://{self.site.domain}/articles/", content)


class SiteFeedScopeTest(TestCase):
	"""
	Both site-scoped feeds are scoped to the REQUESTED SITE's
	scope_subjects -- not team ownership, and not the caller's own
	visibility (Phase 5). This is the behaviour change from the pre-Phase-5
	feeds, which read gregory.visibility.visible_subject_ids(request) and
	therefore varied by who was asking.
	"""

	def setUp(self):
		self.org = Organization.objects.create(name="Feed Org", slug="feed-org-p5")
		self.team = Team.objects.create(
			name="Feed Team", organization=self.org, slug="feed-team-p5"
		)
		self.published = Subject.objects.create(
			subject_name="Published", subject_slug="published-p5", team=self.team
		)
		# Owned by the same team, but never curated into this site's scope.
		self.uncurated = Subject.objects.create(
			subject_name="Uncurated", subject_slug="uncurated-p5", team=self.team
		)
		# No team at all -- curation is the whole grant, so this resolves
		# like any other subject once a site's scope names it (pinned below).
		self.teamless = Subject.objects.create(
			subject_name="Teamless", subject_slug="teamless-p5", team=None
		)

		self.site = Site.objects.create(domain="feeds-p5.example.com", name="Feeds P5")
		self.settings_row = CustomSetting.objects.create(
			site=self.site, title="Feed settings P5", rss_enabled=True
		)
		self.settings_row.scope_subjects.add(self.published)

		for subject in (self.published, self.uncurated, self.teamless):
			trial = Trials.objects.create(
				title=f"trial-{subject.subject_slug}",
				link=f"https://registry.example.org/{subject.subject_slug}",
			)
			trial.teams.add(self.team)
			trial.subjects.add(subject)

	def _trials_feed(self, subject, site_id=None):
		return self.client.get(
			reverse(
				"site_trials_by_subject_feed",
				kwargs={
					"site_id": site_id if site_id is not None else self.site.pk,
					"subject_slug": subject.subject_slug,
				},
			)
		)

	def _author_feed(self, orcid, site_id=None):
		return self.client.get(
			reverse(
				"site_articles_by_author_feed",
				kwargs={
					"site_id": site_id if site_id is not None else self.site.pk,
					"orcid": orcid,
				},
			)
		)

	def test_subject_in_the_requested_sites_scope_is_served(self):
		response = self._trials_feed(self.published)
		self.assertEqual(response.status_code, 200)
		self.assertIn("trial-published-p5", response.content.decode())

	def test_subject_owned_by_the_same_team_but_uncurated_404s(self):
		self.assertEqual(self._trials_feed(self.uncurated).status_code, 404)

	def test_subject_with_no_team_404s_until_curated(self):
		self.assertEqual(self._trials_feed(self.teamless).status_code, 404)

	def test_subject_with_no_team_is_served_once_this_site_curates_it(self):
		self.settings_row.scope_subjects.add(self.teamless)
		response = self._trials_feed(self.teamless)
		self.assertEqual(response.status_code, 200)
		self.assertIn("trial-teamless-p5", response.content.decode())

	def test_unknown_site_id_404s(self):
		self.assertEqual(self._trials_feed(self.published, site_id=999999).status_code, 404)

	def test_site_with_rss_disabled_404s(self):
		self.settings_row.rss_enabled = False
		self.settings_row.save()
		self.assertEqual(self._trials_feed(self.published).status_code, 404)

	def test_site_with_no_customsetting_404s(self):
		bare_site = Site.objects.create(domain="bare-p5.example.com", name="Bare P5")
		self.assertEqual(self._trials_feed(self.published, site_id=bare_site.pk).status_code, 404)

	def test_response_does_not_vary_by_caller(self):
		"""
		The defining property of Phase 5: unlike the pre-Phase-5 feeds, an
		anonymous caller, a signed-in member of the owning organisation, and
		a caller with no relationship to it at all see exactly the same
		body -- because the scope is the requested site's, not theirs.
		"""
		from django.contrib.auth import get_user_model
		from organizations.models import OrganizationUser

		User = get_user_model()
		user = User.objects.create_user(username="member-p5", password="pw")
		OrganizationUser.objects.create(organization=self.org, user=user)

		anon_body = self._trials_feed(self.published).content.decode()

		self.client.force_login(user)
		member_body = self._trials_feed(self.published).content.decode()
		self.client.logout()

		self.assertEqual(anon_body, member_body)
		# Uncurated content stays absent for the org's own member too --
		# team ownership grants nothing here, matching the org-member case
		# below for the author feed.
		self.assertEqual(self._trials_feed(self.uncurated).status_code, 404)

	def test_author_feed_404s_when_no_article_carries_the_sites_subject(self):
		author = Authors.objects.create(
			given_name="Un", family_name="Seen", ORCID="0000-0002-0000-0019"
		)
		article = Articles.objects.create(
			title="uncurated-article-p5", link="https://example.org/uncurated-p5"
		)
		article.teams.add(self.team)
		article.subjects.add(self.uncurated)
		article.authors.add(author)
		self.assertEqual(self._author_feed(author.ORCID).status_code, 404)

	def test_author_feed_lists_only_this_sites_scope_articles(self):
		author = Authors.objects.create(
			given_name="Mixed", family_name="Bag", ORCID="0000-0002-0000-0020"
		)
		for subject, slug in ((self.published, "shown-p5"), (self.uncurated, "hidden-p5")):
			article = Articles.objects.create(
				title=f"{slug}-article", link=f"https://example.org/{slug}"
			)
			article.teams.add(self.team)
			article.subjects.add(subject)
			article.authors.add(author)
		body = self._author_feed(author.ORCID).content.decode()
		self.assertIn("shown-p5-article", body)
		self.assertNotIn("hidden-p5-article", body)
