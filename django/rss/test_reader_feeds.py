"""
Reader feeds: research by condition (subject), research by topic (category),
recruiting trials and new trials by condition, plus the fixes to the existing
author and trials feeds that share their code (rss/feed_scope.py,
rss/feed_items.py, rss/views.py).

Every test class clears the cache in setUp: the feed routes are cache_page'd
and the test settings use LocMemCache, so a stale entry from one test would
answer the next.
"""

import xml.etree.ElementTree as ET
from datetime import timedelta
from email.utils import parsedate_to_datetime

from django.contrib.sites.models import Site
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from gregory.models import (
	ArticleCategoryAssignment,
	Articles,
	ArticleSiteContent,
	ArticleSubjectRelevance,
	Authors,
	MLPredictions,
	Organization,
	OrganizationApiSettings,
	Subject,
	Team,
	TeamCategory,
	Trials,
)
from sitesettings.models import CustomSetting

DC = "{http://purl.org/dc/elements/1.1/}"


class FeedFixture:
	"""An org, team, one in-scope subject, one out-of-scope subject and a site."""

	def build_fixture(self):
		cache.clear()
		self.org = Organization.objects.create(name="Reader Org", slug="reader-org")
		OrganizationApiSettings.objects.filter(organization=self.org).update(
			make_api_public=True
		)
		self.team = Team.objects.create(
			name="Reader Team", organization=self.org, slug="reader-team"
		)
		# "any": one model reaching the threshold is enough for ML consensus.
		self.subject = Subject.objects.create(
			subject_name="Reader Subject",
			subject_slug="reader-subject",
			team=self.team,
			auto_predict=True,
			ml_consensus_type="any",
		)
		self.other_subject = Subject.objects.create(
			subject_name="Other Subject",
			subject_slug="other-subject",
			team=self.team,
			auto_predict=True,
			ml_consensus_type="any",
		)
		self.site = Site.objects.create(domain="reader.example.com", name="Reader Site")
		self.settings_row = CustomSetting.objects.create(
			site=self.site, title="Reader", rss_enabled=True
		)
		self.settings_row.scope_subjects.add(self.subject)
		self.category = TeamCategory.objects.create(
			team=self.team,
			category_name="Reader Category",
			category_slug="reader-category",
		)
		self.category.subjects.add(self.subject, self.other_subject)

	def make_article(
		self,
		title,
		subjects=None,
		discovery=None,
		published=None,
		retracted=False,
		category=None,
		**extra,
	):
		article = Articles.objects.create(
			title=title,
			link=f"https://example.org/{title}",
			published_date=published,
			retracted=retracted,
			**extra,
		)
		article.subjects.add(*(subjects if subjects is not None else [self.subject]))
		article.teams.add(self.team)
		if discovery is not None:
			# discovery_date is auto_now_add; update() bypasses it.
			Articles.objects.filter(pk=article.pk).update(discovery_date=discovery)
			article.refresh_from_db()
		if category is not None:
			ArticleCategoryAssignment.objects.create(
				articles=article, teamcategory=category
			)
		return article

	def make_trial(self, title, subjects=None, status="Recruiting", discovery=None, **extra):
		trial = Trials.objects.create(
			title=title,
			link=f"https://registry.example.org/{title}",
			recruitment_status=status,
			discovery_date=discovery or timezone.now(),
			**extra,
		)
		trial.teams.add(self.team)
		trial.subjects.add(*(subjects if subjects is not None else [self.subject]))
		return trial

	def ml_yes(self, article, subject=None):
		MLPredictions.objects.create(
			article=article,
			subject=subject or self.subject,
			algorithm="pubmed_bert",
			probability_score=0.95,
			predicted_relevant=True,
		)

	def verdict(self, article, is_relevant, subject=None):
		ArticleSubjectRelevance.objects.create(
			article=article, subject=subject or self.subject, is_relevant=is_relevant
		)

	def url(self, name, **kwargs):
		kwargs.setdefault("site_id", self.site.pk)
		return reverse(name, kwargs=kwargs)

	def subject_url(self, name, subject=None, **kwargs):
		return self.url(
			name, subject_slug=(subject or self.subject).subject_slug, **kwargs
		)

	def category_url(self, name, category=None, **kwargs):
		return self.url(
			name, category_slug=(category or self.category).category_slug, **kwargs
		)

	@staticmethod
	def items(response):
		root = ET.fromstring(response.content)
		return root.findall("./channel/item")

	def titles(self, response):
		return [item.findtext("title") for item in self.items(response)]

	def get_titles(self, url):
		response = self.client.get(url)
		self.assertEqual(response.status_code, 200, url)
		return self.titles(response)


# (route name, uses a subject slug or a category slug)
ARTICLE_SUBJECT_ROUTES = (
	"site_articles_by_subject_feed",
	"site_relevant_articles_by_subject_feed",
)
ARTICLE_CATEGORY_ROUTES = (
	"site_articles_by_category_feed",
	"site_relevant_articles_by_category_feed",
)
TRIAL_SUBJECT_ROUTES = (
	"site_trials_by_subject_feed",
	"site_recruiting_trials_by_subject_feed",
)


class FeedScopeTests(FeedFixture, TestCase):
	def setUp(self):
		self.build_fixture()

	def routes(self, subject=None, category=None):
		"""Every reader feed route, as (name, url) for a subject/category."""
		out = []
		for name in ARTICLE_SUBJECT_ROUTES + TRIAL_SUBJECT_ROUTES:
			out.append((name, self.subject_url(name, subject)))
		for name in ARTICLE_CATEGORY_ROUTES:
			out.append((name, self.category_url(name, category)))
		return out

	def test_in_scope_feeds_are_served_and_empty_feeds_are_200(self):
		for name, url in self.routes():
			with self.subTest(route=name):
				response = self.client.get(url)
				self.assertEqual(response.status_code, 200)
				# In scope but nothing to show: a valid subscription, not a 404.
				self.assertEqual(self.items(response), [])

	def test_unknown_site_404s(self):
		for name, url in self.routes():
			with self.subTest(route=name):
				bad = url.replace(f"/sites/{self.site.pk}/", "/sites/999999/")
				self.assertEqual(self.client.get(bad).status_code, 404)

	def test_site_without_customsetting_404s(self):
		bare = Site.objects.create(domain="bare.example.com", name="Bare")
		for name, url in self.routes():
			with self.subTest(route=name):
				bad = url.replace(f"/sites/{self.site.pk}/", f"/sites/{bare.pk}/")
				self.assertEqual(self.client.get(bad).status_code, 404)

	def test_rss_disabled_404s(self):
		self.settings_row.rss_enabled = False
		self.settings_row.save()
		for name, url in self.routes():
			with self.subTest(route=name):
				self.assertEqual(self.client.get(url).status_code, 404)

	def test_out_of_scope_subject_404s(self):
		for name in ARTICLE_SUBJECT_ROUTES + TRIAL_SUBJECT_ROUTES:
			with self.subTest(route=name):
				url = self.subject_url(name, self.other_subject)
				self.assertEqual(self.client.get(url).status_code, 404)

	def test_unknown_subject_slug_404s(self):
		for name in ARTICLE_SUBJECT_ROUTES + TRIAL_SUBJECT_ROUTES:
			with self.subTest(route=name):
				url = self.url(name, subject_slug="no-such-subject")
				self.assertEqual(self.client.get(url).status_code, 404)

	def test_category_with_no_subject_in_scope_404s(self):
		outside = TeamCategory.objects.create(
			team=self.team, category_name="Outside", category_slug="outside-category"
		)
		outside.subjects.add(self.other_subject)
		for name in ARTICLE_CATEGORY_ROUTES:
			with self.subTest(route=name):
				url = self.category_url(name, outside)
				self.assertEqual(self.client.get(url).status_code, 404)

	def test_unknown_category_slug_404s(self):
		for name in ARTICLE_CATEGORY_ROUTES:
			with self.subTest(route=name):
				url = self.url(name, category_slug="no-such-category")
				self.assertEqual(self.client.get(url).status_code, 404)

	def test_query_string_is_ignored(self):
		article = self.make_article("plain")
		url = self.subject_url("site_articles_by_subject_feed")
		plain = self.client.get(url).content
		self.assertEqual(self.client.get(url + "?ml_threshold=0.99&x=1").content, plain)
		self.assertIn(article.title.encode(), plain)

	def test_response_does_not_vary_by_caller(self):
		from django.contrib.auth import get_user_model
		from organizations.models import OrganizationUser

		self.make_article("plain")
		user = get_user_model().objects.create_user(username="reader-member", password="pw")
		OrganizationUser.objects.create(organization=self.org, user=user)
		url = self.subject_url("site_articles_by_subject_feed")
		anon = self.client.get(url).content
		cache.clear()
		self.client.force_login(user)
		self.assertEqual(self.client.get(url).content, anon)


class SlugResolutionTests(FeedFixture, TestCase):
	def setUp(self):
		self.build_fixture()
		# A second team using the very same slug (allowed: unique per team).
		other_org = Organization.objects.create(name="Dup Org", slug="dup-org")
		self.other_team = Team.objects.create(
			name="Dup Team", organization=other_org, slug="dup-team"
		)
		self.twin = Subject.objects.create(
			subject_name="Twin",
			subject_slug=self.subject.subject_slug,
			team=self.other_team,
		)
		self.make_article("ours")
		self.make_trial("our-trial")
		twin_trial = self.make_trial("twin-trial", subjects=[self.twin])
		twin_trial.teams.add(self.other_team)

	def test_scoped_slug_resolves_to_the_subject_in_scope(self):
		# Unscoped Subject.objects.get(subject_slug=...) raised
		# MultipleObjectsReturned (a 500) here.
		self.assertEqual(
			self.get_titles(self.subject_url("site_trials_by_subject_feed")), ["our-trial"]
		)
		self.assertEqual(
			self.get_titles(self.subject_url("site_recruiting_trials_by_subject_feed")),
			["our-trial"],
		)
		self.assertEqual(
			self.get_titles(self.subject_url("site_articles_by_subject_feed")), ["ours"]
		)
		self.assertEqual(
			self.get_titles(self.subject_url("site_relevant_articles_by_subject_feed")), []
		)

	def test_two_subjects_in_the_same_scope_with_one_slug_404s_and_warns(self):
		self.settings_row.scope_subjects.add(self.twin)
		for name in ARTICLE_SUBJECT_ROUTES + TRIAL_SUBJECT_ROUTES:
			with self.subTest(route=name):
				cache.clear()
				with self.assertLogs("rss.feed_scope", level="WARNING") as logs:
					response = self.client.get(self.subject_url(name))
				self.assertEqual(response.status_code, 404)
				self.assertIn(self.subject.subject_slug, logs.output[0])


class CategoryFeedTests(FeedFixture, TestCase):
	def setUp(self):
		self.build_fixture()

	def test_lists_articles_in_the_category_only(self):
		self.make_article("in-cat", category=self.category)
		self.make_article("not-in-cat")
		self.assertEqual(
			self.get_titles(self.category_url("site_articles_by_category_feed")),
			["in-cat"],
		)

	def test_article_in_category_through_an_uncovered_subject_does_not_leak(self):
		# Tagged only with a subject the site doesn't cover, though the
		# category itself spans both.
		self.make_article(
			"leak", subjects=[self.other_subject], category=self.category
		)
		self.make_article("ok", category=self.category)
		for name in ARTICLE_CATEGORY_ROUTES:
			with self.subTest(route=name):
				cache.clear()
				self.assertNotIn("leak", self.get_titles(self.category_url(name)))
		self.assertEqual(
			self.get_titles(self.category_url("site_articles_by_category_feed")), ["ok"]
		)

	def test_relevance_is_judged_on_the_categorys_in_scope_subjects(self):
		# ML yes only for a subject outside the site's scope: not relevant here.
		outside_ml = self.make_article(
			"outside-ml",
			subjects=[self.subject, self.other_subject],
			category=self.category,
		)
		self.ml_yes(outside_ml, self.other_subject)
		inside_ml = self.make_article("inside-ml", category=self.category)
		self.ml_yes(inside_ml)
		self.assertEqual(
			self.get_titles(self.category_url("site_relevant_articles_by_category_feed")),
			["inside-ml"],
		)

	def test_channel_titles_and_link(self):
		response = self.client.get(self.category_url("site_articles_by_category_feed"))
		root = ET.fromstring(response.content)
		self.assertEqual(root.findtext("./channel/title"), "Reader Category — Reader Site")
		self.assertEqual(root.findtext("./channel/link"), "https://reader.example.com/")
		cache.clear()
		response = self.client.get(
			self.category_url("site_relevant_articles_by_category_feed")
		)
		root = ET.fromstring(response.content)
		self.assertEqual(
			root.findtext("./channel/title"), "Relevant research: Reader Category — Reader Site"
		)


class RelevantVariantTests(FeedFixture, TestCase):
	def setUp(self):
		self.build_fixture()
		self.editor_yes = self.make_article("editor-yes")
		self.verdict(self.editor_yes, True)
		self.ml_only = self.make_article("ml-only")
		self.ml_yes(self.ml_only)
		self.plain = self.make_article("plain")
		self.editor_no = self.make_article("editor-no")
		self.ml_yes(self.editor_no)
		self.verdict(self.editor_no, False)
		self.other_verdict = self.make_article("other-verdict")
		self.verdict(self.other_verdict, True, subject=self.other_subject)

	def test_subject_relevant_feed(self):
		titles = self.get_titles(self.subject_url("site_relevant_articles_by_subject_feed"))
		self.assertCountEqual(titles, ["editor-yes", "ml-only"])

	def test_all_variant_is_not_filtered_by_relevance(self):
		titles = self.get_titles(self.subject_url("site_articles_by_subject_feed"))
		self.assertCountEqual(
			titles,
			["editor-yes", "ml-only", "plain", "editor-no", "other-verdict"],
		)

	def test_category_relevant_feed(self):
		for article in (self.editor_yes, self.ml_only, self.plain, self.editor_no):
			ArticleCategoryAssignment.objects.create(
				articles=article, teamcategory=self.category
			)
		titles = self.get_titles(
			self.category_url("site_relevant_articles_by_category_feed")
		)
		self.assertCountEqual(titles, ["editor-yes", "ml-only"])

	def test_channel_title(self):
		response = self.client.get(self.subject_url("site_relevant_articles_by_subject_feed"))
		root = ET.fromstring(response.content)
		self.assertEqual(
			root.findtext("./channel/title"), "Relevant Reader Subject research — Reader Site"
		)


class RetractedTests(FeedFixture, TestCase):
	def setUp(self):
		self.build_fixture()
		self.live = self.make_article("live", category=self.category)
		self.retracted = self.make_article("retracted", retracted=True, category=self.category)
		self.author = Authors.objects.create(
			given_name="Jane", family_name="Doe", ORCID="0000-0002-7922-9785"
		)
		self.live.authors.add(self.author)
		self.retracted.authors.add(self.author)

	def test_retracted_is_excluded_from_every_article_feed(self):
		urls = [
			self.subject_url("site_articles_by_subject_feed"),
			self.category_url("site_articles_by_category_feed"),
			self.url("site_articles_by_author_feed", orcid=self.author.ORCID),
		]
		for url in urls:
			with self.subTest(url=url):
				cache.clear()
				self.assertEqual(self.get_titles(url), ["live"])

	def test_retracted_is_excluded_from_relevant_feeds(self):
		self.verdict(self.retracted, True)
		self.assertEqual(
			self.get_titles(self.subject_url("site_relevant_articles_by_subject_feed")), []
		)

	def test_author_with_only_retracted_articles_404s(self):
		only = Authors.objects.create(
			given_name="Only", family_name="Retracted", ORCID="0000-0002-0000-0001"
		)
		self.retracted.authors.add(only)
		response = self.client.get(self.url("site_articles_by_author_feed", orcid=only.ORCID))
		self.assertEqual(response.status_code, 404)


class RecruitingFeedTests(FeedFixture, TestCase):
	def setUp(self):
		self.build_fixture()
		self.by_status = {
			"Recruiting": self.make_trial("t-recruiting", status="Recruiting"),
			"not_yet_recruiting": self.make_trial("t-not-yet", status="not_yet_recruiting"),
			"enrolling_by_invitation": self.make_trial("t-invitation", status="enrolling_by_invitation"),
			"Completed": self.make_trial("t-completed", status="Completed"),
			"Terminated": self.make_trial("t-terminated", status="Terminated"),
			None: self.make_trial("t-null", status=None),
		}

	def test_normalised_statuses_are_what_we_expect(self):
		# Guards the fixture: the feed filters on the normalised column.
		self.assertEqual(
			{
				raw: trial.recruitment_status_normalized
				for raw, trial in self.by_status.items()
			},
			{
				"Recruiting": "recruiting",
				"not_yet_recruiting": "not_yet_recruiting",
				"enrolling_by_invitation": "enrolling_by_invitation",
				"Completed": "completed",
				"Terminated": "terminated",
				None: None,
			},
		)

	def test_recruiting_feed_has_only_recruiting_and_not_yet_recruiting(self):
		titles = self.get_titles(self.subject_url("site_recruiting_trials_by_subject_feed"))
		self.assertCountEqual(titles, ["t-recruiting", "t-not-yet"])

	def test_plain_trials_feed_still_has_everything(self):
		titles = self.get_titles(self.subject_url("site_trials_by_subject_feed"))
		self.assertEqual(len(titles), 6)

	def test_channel_titles(self):
		for name, expected in (
			("site_trials_by_subject_feed", "Reader Subject clinical trials — Reader Site"),
			(
				"site_recruiting_trials_by_subject_feed",
				"Recruiting Reader Subject trials — Reader Site",
			),
		):
			with self.subTest(route=name):
				cache.clear()
				root = ET.fromstring(self.client.get(self.subject_url(name)).content)
				self.assertEqual(root.findtext("./channel/title"), expected)
				# The trials feeds keep linking to the subject's trials page.
				self.assertEqual(
					root.findtext("./channel/link"),
					"https://reader.example.com/trials/subject/reader-subject/",
				)


class OrderAndDateTests(FeedFixture, TestCase):
	def setUp(self):
		self.build_fixture()
		self.now = timezone.now()

	def test_articles_order_by_discovery_and_pubdate_is_discovery(self):
		found_today = self.make_article(
			"found-today-published-2019",
			discovery=self.now,
			published=self.now.replace(year=2019),
		)
		self.make_article(
			"found-last-week-published-yesterday",
			discovery=self.now - timedelta(days=7),
			published=self.now - timedelta(days=1),
		)
		response = self.client.get(self.subject_url("site_articles_by_subject_feed"))
		self.assertEqual(
			self.titles(response),
			["found-today-published-2019", "found-last-week-published-yesterday"],
		)
		pub = parsedate_to_datetime(self.items(response)[0].findtext("pubDate"))
		self.assertEqual(pub.replace(microsecond=0), found_today.discovery_date.replace(microsecond=0))

	def test_author_feed_keeps_publication_order_and_pubdate(self):
		author = Authors.objects.create(
			given_name="Jane", family_name="Doe", ORCID="0000-0002-7922-9785"
		)
		old_found_today = self.make_article(
			"old-found-today", discovery=self.now, published=self.now.replace(year=2019)
		)
		new_found_earlier = self.make_article(
			"new-found-earlier",
			discovery=self.now - timedelta(days=30),
			published=self.now - timedelta(days=1),
		)
		undated = self.make_article("undated", discovery=self.now)
		for article in (old_found_today, new_found_earlier, undated):
			article.authors.add(author)
		response = self.client.get(self.url("site_articles_by_author_feed", orcid=author.ORCID))
		self.assertEqual(self.titles(response), ["new-found-earlier", "old-found-today", "undated"])
		pub = parsedate_to_datetime(self.items(response)[1].findtext("pubDate"))
		self.assertEqual(pub.year, 2019)

	def test_trial_pubdate_is_discovery_date_and_order_is_discovery(self):
		recent = self.make_trial(
			"recent", discovery=self.now, published_date=self.now.replace(year=2015)
		)
		self.make_trial(
			"older", discovery=self.now - timedelta(days=3), published_date=self.now
		)
		undated = self.make_trial("undated")
		Trials.objects.filter(pk=undated.pk).update(discovery_date=None)
		response = self.client.get(self.subject_url("site_trials_by_subject_feed"))
		# An undated trial must not pin to the top.
		self.assertEqual(self.titles(response), ["recent", "older", "undated"])
		pub = parsedate_to_datetime(self.items(response)[0].findtext("pubDate"))
		self.assertEqual(pub.replace(microsecond=0), recent.discovery_date.replace(microsecond=0))

	def test_article_feeds_are_capped_at_100_and_trials_at_50(self):
		Articles.objects.bulk_create(
			[Articles(title=f"bulk-{i}", link=f"https://example.org/bulk-{i}") for i in range(101)]
		)
		through = Articles.subjects.through
		through.objects.bulk_create(
			[
				through(articles_id=pk, subject_id=self.subject.pk)
				for pk in Articles.objects.filter(title__startswith="bulk-").values_list("pk", flat=True)
			]
		)
		self.assertEqual(
			len(self.get_titles(self.subject_url("site_articles_by_subject_feed"))), 100
		)


class EditorialContentTests(FeedFixture, TestCase):
	def setUp(self):
		self.build_fixture()
		self.other_site = Site.objects.create(domain="other-site.example.com", name="Other")
		self.article = self.make_article(
			"edited",
			summary="Abstract <script>alert(1)</script>\nsecond line",
			container_title="Journal of Tests",
			access="open",
			published=timezone.now().replace(year=2024, month=3, day=9),
		)
		ArticleSiteContent.objects.create(
			article=self.article,
			site=self.site,
			takeaways="Takeaway <b>bold</b>",
			summary_plain_english="Plain <script>x</script> english",
		)
		ArticleSiteContent.objects.create(
			article=self.article,
			site=self.other_site,
			takeaways="OTHER-SITE-TAKEAWAY",
			summary_plain_english="OTHER-SITE-PLAIN",
		)

	def description(self):
		response = self.client.get(self.subject_url("site_articles_by_subject_feed"))
		return self.items(response)[0].findtext("description")

	def test_this_sites_content_appears_and_another_sites_does_not(self):
		description = self.description()
		self.assertIn("In plain English", description)
		self.assertIn("Key takeaways", description)
		self.assertNotIn("OTHER-SITE", description)

	def test_sections_come_in_the_documented_order(self):
		description = self.description()
		positions = [
			description.index(marker)
			for marker in ("In plain English", "Key takeaways", "Journal of Tests", "Abstract")
		]
		self.assertEqual(positions, sorted(positions))
		self.assertIn("Journal of Tests · 2024-03-09 · Open access", description)

	def test_html_in_editorial_text_and_abstract_is_escaped(self):
		description = self.description()
		self.assertNotIn("<script>", description)
		self.assertNotIn("<b>bold</b>", description)
		self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", description)
		self.assertIn("&lt;b&gt;bold&lt;/b&gt;", description)
		# line breaks become <br>
		self.assertIn("<br>second line", description)

	def test_without_editorial_content_the_item_is_metadata_and_abstract(self):
		bare = self.make_article("bare", summary="Just an abstract", container_title="J Bare")
		response = self.client.get(self.subject_url("site_articles_by_subject_feed"))
		item = next(i for i in self.items(response) if i.findtext("title") == "bare")
		description = item.findtext("description")
		self.assertNotIn("In plain English", description)
		self.assertNotIn("Key takeaways", description)
		self.assertIn("J Bare", description)
		self.assertIn("Just an abstract", description)
		self.assertTrue(bare.pk)

	def test_trial_summary_and_fields_are_escaped(self):
		self.make_trial(
			"xss-trial",
			summary="Summary <script>alert(1)</script>",
			primary_sponsor="Sponsor <img src=x>",
			condition="<i>cond</i>",
		)
		response = self.client.get(self.subject_url("site_trials_by_subject_feed"))
		description = self.items(response)[0].findtext("description")
		self.assertNotIn("<script>", description)
		self.assertNotIn("<img", description)
		self.assertNotIn("<i>cond</i>", description)
		self.assertIn("&lt;script&gt;", description)
		# Our own markup is still there.
		self.assertIn("<strong>Sponsor:</strong>", description)


class ItemElementTests(FeedFixture, TestCase):
	def setUp(self):
		self.build_fixture()

	def test_creator_lists_three_authors_in_insertion_order_then_et_al(self):
		article = self.make_article("many-authors")
		authors = [
			Authors.objects.create(given_name=name, family_name="Smith")
			for name in ("Zed", "Amy", "Bob", "Cat")
		]
		for author in authors:
			article.authors.add(author)
		response = self.client.get(self.subject_url("site_articles_by_subject_feed"))
		self.assertEqual(
			self.items(response)[0].findtext(f"{DC}creator"),
			"Zed Smith, Amy Smith, Bob Smith et al.",
		)

	def test_no_authors_means_no_creator(self):
		self.make_article("no-authors")
		response = self.client.get(self.subject_url("site_articles_by_subject_feed"))
		self.assertIsNone(self.items(response)[0].find(f"{DC}creator"))

	def test_article_categories_are_in_scope_subjects_then_categories(self):
		self.make_article(
			"cats", subjects=[self.subject, self.other_subject], category=self.category
		)
		response = self.client.get(self.subject_url("site_articles_by_subject_feed"))
		self.assertEqual(
			[c.text for c in self.items(response)[0].findall("category")],
			# other_subject isn't in the site's scope, so it is not advertised.
			["Reader Subject", "Reader Category"],
		)

	def test_trial_categories_are_phase_status_then_subject(self):
		self.make_trial("phased", phase="Phase 2", status="Recruiting")
		response = self.client.get(self.subject_url("site_trials_by_subject_feed"))
		self.assertEqual(
			[c.text for c in self.items(response)[0].findall("category")],
			["Phase 2", "Recruiting", "Reader Subject"],
		)

	def test_article_link_and_guid(self):
		article = self.make_article("linked", doi="10.1/linked")
		response = self.client.get(self.subject_url("site_articles_by_subject_feed"))
		item = self.items(response)[0]
		self.assertEqual(
			item.findtext("link"), f"https://reader.example.com/articles/{article.pk}/"
		)
		self.assertEqual(item.findtext("guid"), "doi:10.1/linked")


class TrialLinkTests(FeedFixture, TestCase):
	def setUp(self):
		self.build_fixture()
		self.trial = self.make_trial("linkable")

	def link(self):
		cache.clear()
		response = self.client.get(self.subject_url("site_trials_by_subject_feed"))
		return self.items(response)[0].findtext("link")

	def test_registry_link_when_the_site_has_no_trial_pages(self):
		self.assertEqual(self.link(), self.trial.link)

	def test_site_trial_page_when_the_site_has_trial_pages(self):
		self.settings_row.sitemap_include_trials = True
		self.settings_row.save()
		self.assertEqual(
			self.link(), f"https://reader.example.com/trials/{self.trial.trial_id}/"
		)

	def test_recruiting_feed_uses_the_same_rule(self):
		self.settings_row.sitemap_include_trials = True
		self.settings_row.save()
		response = self.client.get(self.subject_url("site_recruiting_trials_by_subject_feed"))
		self.assertEqual(
			self.items(response)[0].findtext("link"),
			f"https://reader.example.com/trials/{self.trial.trial_id}/",
		)


class QueryCountTests(FeedFixture, TestCase):
	"""A feed costs a fixed number of queries however many items it has."""

	def setUp(self):
		self.build_fixture()
		self.big_subject = Subject.objects.create(
			subject_name="Big Subject",
			subject_slug="big-subject",
			team=self.team,
			auto_predict=True,
		)
		self.settings_row.scope_subjects.add(self.big_subject)
		self.big_category = TeamCategory.objects.create(
			team=self.team, category_name="Big Category", category_slug="big-category"
		)
		self.big_category.subjects.add(self.big_subject)

		self.author = Authors.objects.create(
			given_name="Jane", family_name="Doe", ORCID="0000-0002-7922-9785"
		)
		self.big_author = Authors.objects.create(
			given_name="Big", family_name="Author", ORCID="0000-0002-0000-0002"
		)
		self._populate(self.subject, self.category, self.author, 3)
		self._populate(self.big_subject, self.big_category, self.big_author, 30)

	def _populate(self, subject, category, author, count):
		for i in range(count):
			article = self.make_article(
				f"{subject.subject_slug}-article-{i}", subjects=[subject], category=category
			)
			article.authors.add(author)
			article.authors.add(Authors.objects.create(given_name=f"Co{i}", family_name="Author"))
			ArticleSiteContent.objects.create(
				article=article, site=self.site, takeaways=f"t{i}", summary_plain_english=f"p{i}"
			)
			self.ml_yes(article, subject)
			trial = self.make_trial(f"{subject.subject_slug}-trial-{i}", subjects=[subject])
			trial.subjects.add(subject)

	def count_queries(self, url):
		# Warm per-process caches (Site.objects.get_current in Feed.get_feed)
		# so the first feed measured doesn't pay a one-off extra query.
		self.client.get(url)
		cache.clear()
		with CaptureQueriesContext(connection) as ctx:
			response = self.client.get(url)
		self.assertEqual(response.status_code, 200)
		return len(ctx), len(self.items(response))

	def assert_flat(self, small_url, big_url, small_items, big_items):
		small_queries, small_count = self.count_queries(small_url)
		big_queries, big_count = self.count_queries(big_url)
		self.assertEqual((small_count, big_count), (small_items, big_items))
		self.assertEqual(small_queries, big_queries)

	def test_subject_feed(self):
		self.assert_flat(
			self.subject_url("site_articles_by_subject_feed"),
			self.subject_url("site_articles_by_subject_feed", self.big_subject),
			3,
			30,
		)

	def test_relevant_subject_feed(self):
		self.assert_flat(
			self.subject_url("site_relevant_articles_by_subject_feed"),
			self.subject_url("site_relevant_articles_by_subject_feed", self.big_subject),
			3,
			30,
		)

	def test_category_feed(self):
		self.assert_flat(
			self.category_url("site_articles_by_category_feed"),
			self.category_url("site_articles_by_category_feed", self.big_category),
			3,
			30,
		)

	def test_author_feed(self):
		self.assert_flat(
			self.url("site_articles_by_author_feed", orcid=self.author.ORCID),
			self.url("site_articles_by_author_feed", orcid=self.big_author.ORCID),
			3,
			30,
		)

	def test_trials_feeds(self):
		for name in TRIAL_SUBJECT_ROUTES:
			with self.subTest(route=name):
				self.assert_flat(
					self.subject_url(name),
					self.subject_url(name, self.big_subject),
					3,
					30,
				)


class FeedCachingTests(FeedFixture, TestCase):
	def setUp(self):
		self.build_fixture()
		self.make_article("first")
		self.make_trial("first-trial")

	def test_second_request_is_served_from_cache_until_it_is_cleared(self):
		for name in ("site_articles_by_subject_feed", "site_trials_by_subject_feed"):
			with self.subTest(route=name):
				cache.clear()
				url = self.subject_url(name)
				before = self.get_titles(url)
				if name == "site_articles_by_subject_feed":
					self.make_article(f"second-{name}")
				else:
					self.make_trial(f"second-{name}")
				self.assertEqual(self.get_titles(url), before)
				cache.clear()
				self.assertEqual(len(self.get_titles(url)), len(before) + 1)

	def test_404_is_not_cached(self):
		url = self.url("site_articles_by_subject_feed", subject_slug="late-subject")
		self.assertEqual(self.client.get(url).status_code, 404)
		late = Subject.objects.create(
			subject_name="Late", subject_slug="late-subject", team=self.team
		)
		self.settings_row.scope_subjects.add(late)
		self.assertEqual(self.client.get(url).status_code, 200)
