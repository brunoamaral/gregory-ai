"""
Search feeds (feed/sites/<site_id>/{articles,trials}/search/) and robots.txt.

Reuses the fixtures of the reader-feed tests. Every class clears the cache in
setUp: the search feeds cache themselves in the default cache, which is a
LocMemCache under test.
"""

import xml.etree.ElementTree as ET
from datetime import timedelta

from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from api.tests.visibility_helpers import publish_subjects
from gregory.models import (
	ArticleSiteContent,
	Sponsor,
	TeamCategory,
	Trials,
)
from rss.search_feeds import (
	ARTICLE_SEARCH_PARAMS,
	TRIAL_SEARCH_PARAMS,
	canonical_params,
)
from rss.test_reader_feeds import FeedFixture
from sitesettings.models import CustomSetting

ATOM = "{http://www.w3.org/2005/Atom}"
ARTICLES = "site_articles_search_feed"
TRIALS = "site_trials_search_feed"


class SearchFeedBase(FeedFixture):
	def build(self):
		self.build_fixture()
		self.now = timezone.now()

	def feed(self, name, query="", **kwargs):
		url = self.url(name, **kwargs)
		return self.client.get(f"{url}?{query}" if query else url)

	def feed_titles(self, name, query="", expect=200):
		response = self.feed(name, query)
		self.assertEqual(response.status_code, expect, query)
		return self.titles(response) if expect == 200 else None

	def channel(self, response):
		return ET.fromstring(response.content).find("./channel")


class ArticleSearchFeedTests(SearchFeedBase, TestCase):
	def setUp(self):
		self.build()

	# --- base set and scope -------------------------------------------------

	def test_no_parameters_returns_the_whole_scope_minus_retracted(self):
		self.make_article("In scope", discovery=self.now)
		self.make_article("Retracted", retracted=True, discovery=self.now)
		self.make_article("Other subject", subjects=[self.other_subject], discovery=self.now)
		self.assertEqual(self.feed_titles(ARTICLES), ["In scope"])

	def test_ordered_by_discovery_date_with_pubdate_to_match(self):
		old = self.make_article(
			"Old but just found",
			discovery=self.now,
			published=self.now - timedelta(days=900),
		)
		self.make_article("Newer paper, found earlier", discovery=self.now - timedelta(days=2), published=self.now)
		self.assertEqual(
			self.feed_titles(ARTICLES), ["Old but just found", "Newer paper, found earlier"]
		)
		pub = self.items(self.feed(ARTICLES))[0].findtext("pubDate")
		self.assertIn(str(old.discovery_date.year), pub)

	def test_limited_to_fifty_items(self):
		for i in range(51):
			self.make_article(f"Item {i:02d}", discovery=self.now - timedelta(minutes=i))
		self.assertEqual(len(self.feed_titles(ARTICLES)), 50)

	def test_scope_404s(self):
		self.assertEqual(self.client.get(reverse(ARTICLES, kwargs={"site_id": 99999})).status_code, 404)
		other = Site.objects.create(domain="nosettings.example.com", name="No settings")
		self.assertEqual(self.client.get(reverse(ARTICLES, kwargs={"site_id": other.pk})).status_code, 404)
		self.settings_row.rss_enabled = False
		self.settings_row.save()
		self.assertEqual(self.feed(ARTICLES).status_code, 404)

	def test_subject_outside_scope_is_404(self):
		self.assertEqual(self.feed(ARTICLES, f"subject_id={self.other_subject.pk}").status_code, 404)

	def test_category_outside_scope_or_unknown_is_404(self):
		outside = TeamCategory.objects.create(
			team=self.team, category_name="Outside", category_slug="outside"
		)
		outside.subjects.add(self.other_subject)
		self.assertEqual(self.feed(ARTICLES, "category_slug=outside").status_code, 404)
		self.assertEqual(self.feed(ARTICLES, "category_slug=nope").status_code, 404)

	# --- parameters -----------------------------------------------------------

	def test_each_parameter_narrows_like_the_api(self):
		"""The same fixture through /articles/ and through the feed."""
		publish_subjects(self.subject, self.other_subject, organization=self.org)
		self.make_article(
			"Remyelination of axons", discovery=self.now, access="open", summary="myelin repair"
		)
		self.make_article("Remyelination review", discovery=self.now, access="restricted")
		self.make_article("Unrelated topic", discovery=self.now, access="open", category=self.category)
		self.make_article("Remyelination in category", discovery=self.now, access="open", category=self.category)
		cases = [
			"search=remyelination",
			"search=remyelination&open_access=true",
			"open_access=false",
			"title=review",
			"summary=myelin",
			f"category_slug={self.category.category_slug}",
		]
		for query in cases:
			with self.subTest(query=query):
				api = self.client.get(f"/articles/?subject_id={self.subject.pk}&{query}")
				self.assertEqual(api.status_code, 200)
				api_titles = {row["title"] for row in api.json()["results"]}
				self.assertTrue(api_titles, query)  # not vacuous
				self.assertEqual(set(self.feed_titles(ARTICLES, query)), api_titles)

	def test_has_takeaways_means_this_sites_takeaways(self):
		with_mine = self.make_article("Mine", discovery=self.now)
		with_theirs = self.make_article("Theirs", discovery=self.now)
		self.make_article("Plain", discovery=self.now)
		other_site = Site.objects.create(domain="other.example.com", name="Other")
		ArticleSiteContent.objects.create(article=with_mine, site=self.site, takeaways="Mine matters")
		ArticleSiteContent.objects.create(article=with_theirs, site=other_site, takeaways="Theirs matters")
		self.assertEqual(self.feed_titles(ARTICLES, "has_takeaways=true"), ["Mine"])
		self.assertEqual(
			sorted(self.feed_titles(ARTICLES, "has_takeaways=false")), ["Plain", "Theirs"]
		)

	def test_has_takeaways_does_not_depend_on_the_caller(self):
		from django.contrib.auth import get_user_model

		article = self.make_article("Mine", discovery=self.now)
		ArticleSiteContent.objects.create(article=article, site=self.site, takeaways="x")
		anonymous = self.feed_titles(ARTICLES, "has_takeaways=true")
		user = get_user_model().objects.create_user("reader")
		self.client.force_login(user)
		cache.clear()
		self.assertEqual(self.feed_titles(ARTICLES, "has_takeaways=true"), anonymous)

	def test_subject_and_category_narrow_the_scope(self):
		both = self.make_article("In category", discovery=self.now, category=self.category)
		self.make_article("Not in category", discovery=self.now)
		self.assertEqual(
			self.feed_titles(ARTICLES, f"subject_id={self.subject.pk}&category_slug={self.category.category_slug}"),
			[both.title],
		)

	def test_category_does_not_leak_through_a_subject_the_site_does_not_cover(self):
		self.make_article(
			"Only elsewhere", subjects=[self.other_subject], discovery=self.now, category=self.category
		)
		self.make_article("Here", discovery=self.now, category=self.category)
		self.assertEqual(
			self.feed_titles(ARTICLES, f"category_slug={self.category.category_slug}"), ["Here"]
		)

	def test_subject_that_is_not_one_of_the_categorys_is_an_empty_feed(self):
		narrow = TeamCategory.objects.create(team=self.team, category_name="Narrow", category_slug="narrow")
		narrow.subjects.add(self.other_subject)
		# Make the site cover the category through a second subject, so the
		# category is in scope while the picked subject is not one of its own.
		self.settings_row.scope_subjects.add(self.other_subject)
		self.make_article("Tagged", discovery=self.now, category=narrow)
		self.assertEqual(
			self.feed_titles(ARTICLES, f"subject_id={self.subject.pk}&category_slug=narrow"), []
		)

	# --- relevance --------------------------------------------------------------

	def test_relevant_uses_the_editorial_rule(self):
		editor_yes = self.make_article("Editor yes", discovery=self.now)
		self.verdict(editor_yes, True)
		ml = self.make_article("ML yes", discovery=self.now)
		self.ml_yes(ml)
		overruled = self.make_article("ML yes editor no", discovery=self.now)
		self.ml_yes(overruled)
		self.verdict(overruled, False)
		self.make_article("Plain", discovery=self.now)

		self.assertEqual(
			sorted(self.feed_titles(ARTICLES, "relevant=true")), ["Editor yes", "ML yes"]
		)
		self.assertEqual(
			sorted(self.feed_titles(ARTICLES, "relevant=false")), ["ML yes editor no", "Plain"]
		)

	def test_relevant_is_scoped_to_the_sites_subjects(self):
		"""Consensus reached for a subject the site doesn't cover doesn't count,
		even with no subject_id (ArticleFilter would fall back to every subject)."""
		leaky = self.make_article(
			"Relevant elsewhere", subjects=[self.subject, self.other_subject], discovery=self.now
		)
		self.ml_yes(leaky, subject=self.other_subject)
		self.assertEqual(self.feed_titles(ARTICLES, "relevant=true"), [])
		self.assertEqual(self.feed_titles(ARTICLES, "relevant=false"), ["Relevant elsewhere"])

	# --- the allowlist ---------------------------------------------------------

	def test_parameters_off_the_allowlist_have_no_effect(self):
		article = self.make_article("Found", discovery=self.now)
		self.ml_yes(article)  # probability 0.95
		self.make_article("Old", discovery=self.now, published=self.now - timedelta(days=10))
		baseline = sorted(self.feed_titles(ARTICLES, "relevant=true"))
		self.assertEqual(baseline, ["Found"])
		# ml_threshold leaking into the filter would drop the 0.95 prediction.
		noisy = (
			"relevant=true&ml_threshold=0.99&ml_score_min=0.99"
			"&published_date_after=2100-01-01&last_days=1&utm_source=x"
			f"&team_id=999999&site_id=999999&doi=10.1/none&author_id=1&subjects={self.other_subject.pk}"
		)
		self.assertEqual(sorted(self.feed_titles(ARTICLES, noisy)), baseline)
		self.assertEqual(
			sorted(self.feed_titles(ARTICLES, "published_date_after=2100-01-01")), ["Found", "Old"]
		)

	def test_unknown_parameters_and_order_share_one_cache_entry(self):
		self.make_article("First", discovery=self.now)
		first = self.feed_titles(ARTICLES, "search=first&open_access=false")
		self.assertEqual(first, ["First"])
		self.make_article("First again", discovery=self.now)
		# Same canonical query: other order, noise, padding, upper-case bool.
		for query in (
			"open_access=false&search=first",
			"search=first&open_access=false&utm_source=x&ml_threshold=0.1",
			"search=%20first%20&open_access=FALSE&empty=",
		):
			with self.subTest(query=query):
				self.assertEqual(self.feed_titles(ARTICLES, query), first)
		cache.clear()
		self.assertEqual(
			sorted(self.feed_titles(ARTICLES, "search=first&open_access=false")),
			["First", "First again"],
		)

	def test_a_different_search_is_a_different_cache_entry(self):
		self.make_article("Alpha", discovery=self.now)
		self.make_article("Beta", discovery=self.now)
		self.assertEqual(self.feed_titles(ARTICLES, "search=alpha"), ["Alpha"])
		self.assertEqual(self.feed_titles(ARTICLES, "search=beta"), ["Beta"])

	def test_canonical_params_keeps_only_the_allowlist_sorted(self):
		request = self.client.get(
			"/x/?title= b &search=a&utm_source=x&ml_threshold=1&summary=&relevant=TRUE"
		).wsgi_request
		params, canonical = canonical_params(request, ARTICLE_SEARCH_PARAMS)
		self.assertEqual(canonical, "relevant=true&search=a&title=b")
		self.assertEqual(sorted(params.keys()), ["relevant", "search", "title"])
		_params, trial_canonical = canonical_params(request, TRIAL_SEARCH_PARAMS)
		self.assertEqual(trial_canonical, "search=a&title=b")

	# --- errors -------------------------------------------------------------------

	def test_invalid_values_are_400(self):
		bad = [
			"subject_id=abc",
			"subject_id=1.5",
			"subject_id=-1",
			"relevant=maybe",
			"open_access=2",
			"has_takeaways=yes",
			"search=" + "x" * 301,
			"title=" + "x" * 301,
			"summary=" + "x" * 301,
			"category_slug=" + "x" * 301,
		]
		for query in bad:
			with self.subTest(query=query[:40]):
				response = self.feed(ARTICLES, query)
				self.assertEqual(response.status_code, 400)
				self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
		# The limit itself is fine.
		self.assertEqual(self.feed(ARTICLES, "search=" + "x" * 300).status_code, 200)

	def test_a_400_is_not_cached_and_explains_itself(self):
		response = self.feed(ARTICLES, "subject_id=abc")
		self.assertIn("subject_id must be an integer", response.content.decode())
		self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")

	def test_an_unparseable_search_cannot_fail(self):
		"""The boolean parser is best-effort and falls back to a phrase match,
		so there is no search that 400s: it must never 500 either."""
		self.make_article("Plain", discovery=self.now)
		for search in ('(((', '"unclosed', "OR OR", "- - -", ")(", "AND"):
			with self.subTest(search=search):
				self.assertEqual(self.feed(ARTICLES, f"search={search}").status_code, 200)

	def test_post_is_not_allowed(self):
		self.assertEqual(self.client.post(self.url(ARTICLES)).status_code, 405)

	# --- headers, self link, title ----------------------------------------------

	def test_x_robots_tag_on_fresh_and_cached_responses(self):
		self.make_article("A", discovery=self.now)
		for _ in range(2):
			response = self.feed(ARTICLES, "search=a")
			self.assertEqual(response.status_code, 200)
			self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
			self.assertEqual(response["Content-Type"], "application/rss+xml; charset=utf-8")
			self.assertIn("max-age=3600", response["Cache-Control"])

	def test_head_is_served(self):
		self.assertEqual(self.client.head(self.url(ARTICLES)).status_code, 200)

	def test_self_link_carries_the_canonical_query_and_not_noise(self):
		response = self.feed(ARTICLES, "utm_source=x&search=ms&relevant=true")
		channel = self.channel(response)
		self_link = [
			link.get("href") for link in channel.findall(f"{ATOM}link") if link.get("rel") == "self"
		]
		self.assertEqual(
			self_link,
			[f"http://testserver{self.url(ARTICLES)}?relevant=true&search=ms"],
		)

	def test_self_link_without_parameters_has_no_question_mark(self):
		channel = self.channel(self.feed(ARTICLES, "utm_source=x"))
		self_link = [
			link.get("href") for link in channel.findall(f"{ATOM}link") if link.get("rel") == "self"
		]
		self.assertEqual(self_link, [f"http://testserver{self.url(ARTICLES)}"])

	def test_channel_titles(self):
		cases = {
			"": "All articles — Reader Site",
			"search=remyelination": 'Search: "remyelination" — Reader Site',
			f"search=remyelination&subject_id={self.subject.pk}&relevant=true": (
				'Search: "remyelination" · Reader Subject · relevant only — Reader Site'
			),
			"title=ms&summary=repair": 'Search: title: "ms" · abstract: "repair" — Reader Site',
			f"category_slug={self.category.category_slug}&open_access=false&has_takeaways=true": (
				"Search: Reader Category · not open access · with editorial summary — Reader Site"
			),
			"relevant=false&has_takeaways=false": (
				"Search: not marked relevant · without editorial summary — Reader Site"
			),
			"search=two%0Alines": 'Search: "two lines" — Reader Site',
		}
		for query, expected in cases.items():
			with self.subTest(query=query):
				self.assertEqual(self.channel(self.feed(ARTICLES, query)).findtext("title"), expected)

	def test_channel_link_is_the_homepage(self):
		self.assertEqual(
			self.channel(self.feed(ARTICLES)).findtext("link"), "https://reader.example.com/"
		)

	def test_items_carry_this_sites_editorial_content(self):
		article = self.make_article("With text", discovery=self.now)
		ArticleSiteContent.objects.create(
			article=article, site=self.site, takeaways="<script>alert(1)</script>"
		)
		body = self.feed(ARTICLES).content.decode()
		self.assertNotIn("<script>alert(1)</script>", body)
		self.assertIn("Key takeaways", body)


class TrialSearchFeedTests(SearchFeedBase, TestCase):
	def setUp(self):
		self.build()

	def make(self, title, **fields):
		trial = self.make_trial(title, discovery=self.now)
		if fields:
			Trials.objects.filter(pk=trial.pk).update(**fields)
		return trial

	def test_no_parameters_returns_the_whole_scope(self):
		self.make("In scope")
		self.make_trial("Elsewhere", subjects=[self.other_subject])
		self.assertEqual(self.feed_titles(TRIALS), ["In scope"])

	def test_parameters_narrow_like_the_api(self):
		publish_subjects(self.subject, self.other_subject, organization=self.org)
		self.make(
			"Remyelination phase 2",
			recruitment_status_normalized="recruiting",
			phase_normalized="phase_2",
			regions_normalized=["europe"],
			inclusion_age_min_years=18,
			inclusion_age_max_years=65,
			inclusion_gender_normalized="all",
		)
		self.make(
			"Remyelination phase 3",
			recruitment_status_normalized="completed",
			phase_normalized="phase_3",
			regions_normalized=["asia"],
			inclusion_age_min_years=60,
			inclusion_gender_normalized="female",
		)
		self.make("Something else", recruitment_status_normalized="recruiting", phase_normalized="phase_3")
		cases = [
			"search=remyelination",
			"recruitment_status_normalized=recruiting",
			"recruitment_status_normalized=recruiting,completed&phase_normalized=phase_3",
			"phase_normalized=phase_2",
			"region=europe",
			"age_eligible=40",
			"inclusion_gender_normalized=female",
			"title=phase",
		]
		for query in cases:
			with self.subTest(query=query):
				api = self.client.get(f"/trials/?subject_id={self.subject.pk}&{query}")
				self.assertEqual(api.status_code, 200)
				api_titles = {row["title"] for row in api.json()["results"]}
				self.assertTrue(api_titles, query)
				self.assertEqual(set(self.feed_titles(TRIALS, query)), api_titles)

	def test_country_and_sponsor(self):
		from gregory.models import TrialCountry

		sponsor = Sponsor.objects.create(name="Acme Pharma", slug="acme-pharma")
		mine = self.make("By Acme", primary_sponsor_normalized=sponsor)
		TrialCountry.objects.create(trial=mine, country="DE")
		self.make("Other")
		self.assertEqual(self.feed_titles(TRIALS, "sponsor_slug=acme-pharma"), ["By Acme"])
		self.assertEqual(self.feed_titles(TRIALS, "country=de"), ["By Acme"])
		title = self.channel(self.feed(TRIALS, "sponsor_slug=acme-pharma&country=de")).findtext("title")
		self.assertEqual(title, "Search: de · Acme Pharma — Reader Site")
		unknown = self.channel(self.feed(TRIALS, "sponsor_slug=nobody")).findtext("title")
		self.assertEqual(unknown, "Search: nobody — Reader Site")

	def test_article_only_parameters_are_ignored_on_the_trials_feed(self):
		self.make("A trial")
		self.assertEqual(self.feed_titles(TRIALS, "relevant=true&open_access=true&has_takeaways=true"), ["A trial"])

	def test_non_allowlisted_parameters_do_not_filter(self):
		self.make("Old trial", date_registration=(self.now - timedelta(days=900)).date())
		self.assertEqual(
			self.feed_titles(TRIALS, "date_registration_after=2100-01-01&has_results=true&nct=NCT0"),
			["Old trial"],
		)

	def test_invalid_values_are_400(self):
		for query in (
			"region=mars",
			"recruitment_status_normalized=banana",
			"phase_normalized=phase_9",
			"inclusion_gender_normalized=robot",
			"age_eligible=old",
			"age_eligible=-3",
			"age_eligible=1e9",
			"age_eligible=nan",
			"subject_id=abc",
			"search=" + "x" * 301,
			"country=" + "DE," * 120,
		):
			with self.subTest(query=query[:40]):
				self.assertEqual(self.feed(TRIALS, query).status_code, 400)

	def test_scope_404s(self):
		self.assertEqual(self.feed(TRIALS, f"subject_id={self.other_subject.pk}").status_code, 404)
		self.assertEqual(self.client.get(reverse(TRIALS, kwargs={"site_id": 99999})).status_code, 404)

	def test_titles_and_labels(self):
		cases = {
			"": "All clinical trials — Reader Site",
			"recruitment_status_normalized=recruiting,not_yet_recruiting&region=europe": (
				"Search: Recruiting, Not yet recruiting · Europe — Reader Site"
			),
			"phase_normalized=phase_2&age_eligible=40&inclusion_gender_normalized=female": (
				"Search: Phase 2 · Female only · age 40 — Reader Site"
			),
		}
		for query, expected in cases.items():
			with self.subTest(query=query):
				self.assertEqual(self.channel(self.feed(TRIALS, query)).findtext("title"), expected)

	def test_item_link_follows_sitemap_include_trials(self):
		trial = self.make("Linked")
		link = self.items(self.feed(TRIALS))[0].findtext("link")
		self.assertEqual(link, trial.link)
		self.settings_row.sitemap_include_trials = True
		self.settings_row.save()
		cache.clear()
		link = self.items(self.feed(TRIALS))[0].findtext("link")
		self.assertEqual(link, f"https://reader.example.com/trials/{trial.trial_id}/")

	def test_x_robots_tag_and_cache(self):
		self.make("Cached")
		for _ in range(2):
			response = self.feed(TRIALS, "search=cached")
			self.assertEqual(response.status_code, 200)
			self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
		self.make("Cached too")
		self.assertEqual(self.feed_titles(TRIALS, "search=cached&utm_source=x"), ["Cached"])

	def test_two_sites_do_not_share_a_cache_entry(self):
		other = Site.objects.create(domain="second.example.com", name="Second")
		second = CustomSetting.objects.create(site=other, title="Second", rss_enabled=True)
		second.scope_subjects.add(self.other_subject)
		self.make("Mine")
		self.make_trial("Theirs", subjects=[self.other_subject])
		self.assertEqual(self.feed_titles(TRIALS), ["Mine"])
		self.assertEqual(
			self.titles(self.client.get(reverse(TRIALS, kwargs={"site_id": other.pk}))), ["Theirs"]
		)


class RobotsTxtTests(TestCase):
	def test_disallows_the_search_feeds(self):
		response = self.client.get("/robots.txt")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
		lines = response.content.decode().splitlines()
		self.assertEqual(
			lines,
			[
				"User-agent: *",
				"Disallow: /feed/sites/*/articles/search/",
				"Disallow: /feed/sites/*/trials/search/",
				"Disallow: /feed/sites/*/patents/search/",
			],
		)
