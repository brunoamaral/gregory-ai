"""
Patent feeds (feed/sites/<site_id>/patents/{subject,category,search}/).

Reuses the fixtures of the reader-feed tests. Every class clears the cache in
setUp: the subject and category feeds are cache_page'd and the search feed
caches itself, both in the default cache (a LocMemCache under test).

The licence tests use distinctive marker strings in the raw OPS columns
(abstract, claims, inventors, CPC codes, publication numbers) and assert that
they never appear in any of the three feeds.
"""

import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone as dt_timezone
from email.utils import parsedate_to_datetime

from django.contrib.sites.models import Site
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from gregory.models import (
	PatentApplicant,
	PatentCategoryAssignment,
	PatentPublication,
	Patents,
	PatentSiteContent,
	PatentTrialLink,
	Sponsor,
	Subject,
	TeamCategory,
)
from rss.test_reader_feeds import DC, FeedFixture

ATOM = "{http://www.w3.org/2005/Atom}"
BY_SUBJECT = "site_patents_by_subject_feed"
BY_CATEGORY = "site_patents_by_category_feed"
SEARCH = "site_patents_search_feed"

ABSTRACT_MARKER = "ABSTRACTMARKER-xyzzy"
CLAIMS_MARKER = "CLAIMSMARKER-plugh"
INVENTOR_MARKER = "Inventorus Markerson-Quux"
CPC_MARKER = "A61K31/CPCMARKER99"
PUBNUMBER_MARKER = "EP7777777"
RAW_MARKERS = (
	ABSTRACT_MARKER,
	CLAIMS_MARKER,
	INVENTOR_MARKER,
	CPC_MARKER,
	PUBNUMBER_MARKER,
	"Markerson",
)


class PatentFixture(FeedFixture):
	def build(self):
		self.build_fixture()
		self.settings_row.patents_enabled = True
		self.settings_row.save()
		self.now = timezone.now()
		self.sponsor = Sponsor.objects.create(name="Acme Pharma", slug="acme-pharma")
		self.other_sponsor = Sponsor.objects.create(name="Beta Bio", slug="beta-bio")
		self.counter = 0

	def make_patent(
		self,
		title,
		subjects=None,
		published=date(2022, 6, 1),
		category=None,
		discovery=None,
		sponsors=(),
		raw_markers=True,
		**extra,
	):
		self.counter += 1
		fields = {
			"title": title,
			"link": f"https://worldwide.espacenet.com/patent/search?q=pn%3DEP{1000000 + self.counter}A1",
			"earliest_publication_date": published,
		}
		if raw_markers:
			fields.update(
				summary=f"{ABSTRACT_MARKER} remyelination of axons",
				claims=f"1. {CLAIMS_MARKER} a method",
				inventors=[INVENTOR_MARKER],
				cpc_classes=[CPC_MARKER],
				ipc_classes=[CPC_MARKER],
			)
		fields.update(extra)
		patent = Patents.objects.create(**fields)
		patent.subjects.add(*(subjects if subjects is not None else [self.subject]))
		if raw_markers:
			PatentPublication.objects.create(
				patent=patent, publication_number=f"{PUBNUMBER_MARKER}{self.counter:03d}A1"
			)
		for sequence, sponsor in enumerate(sponsors):
			PatentApplicant.objects.create(
				patent=patent, sponsor=sponsor, raw_name=sponsor.name.upper(), sequence=sequence
			)
		if discovery is not None:
			# discovery_date is auto_now_add; update() bypasses it.
			Patents.objects.filter(pk=patent.pk).update(discovery_date=discovery)
			patent.refresh_from_db()
		if category is not None:
			PatentCategoryAssignment.objects.create(patents=patent, teamcategory=category)
		return patent

	def link_trial(self, patent, trial, sponsor=None, **extra):
		extra.setdefault("timing", PatentTrialLink.TIMING_DURING)
		extra.setdefault("basis", PatentTrialLink.BASIS_CATEGORY)
		return PatentTrialLink.objects.create(
			patent=patent, trial=trial, sponsor=sponsor or self.sponsor, **extra
		)

	def description(self, response, index=0):
		return self.items(response)[index].findtext("description")

	def creators(self, response, index=0):
		return self.items(response)[index].findtext(f"{DC}creator")


class PatentFeedScopeTests(PatentFixture, TestCase):
	def setUp(self):
		self.build()

	def routes(self):
		return [
			(BY_SUBJECT, self.subject_url(BY_SUBJECT)),
			(BY_CATEGORY, self.category_url(BY_CATEGORY)),
			(SEARCH, self.url(SEARCH)),
		]

	def test_enabled_empty_feeds_are_200(self):
		for name, url in self.routes():
			with self.subTest(route=name):
				response = self.client.get(url)
				self.assertEqual(response.status_code, 200)
				self.assertEqual(self.items(response), [])

	def test_patents_disabled_is_404_even_with_rss_enabled_and_subject_in_scope(self):
		self.make_patent("Visible")
		self.settings_row.patents_enabled = False
		self.settings_row.save()
		for name, url in self.routes():
			with self.subTest(route=name):
				self.assertEqual(self.client.get(url).status_code, 404)

	def test_rss_disabled_is_404_even_with_patents_enabled(self):
		self.settings_row.rss_enabled = False
		self.settings_row.save()
		for name, url in self.routes():
			with self.subTest(route=name):
				self.assertEqual(self.client.get(url).status_code, 404)

	def test_unknown_site_and_site_without_settings_are_404(self):
		other = Site.objects.create(domain="nosettings.example.com", name="No settings")
		for site_id in (99999, other.pk):
			for name, kwargs in (
				(BY_SUBJECT, {"subject_slug": self.subject.subject_slug}),
				(BY_CATEGORY, {"category_slug": self.category.category_slug}),
				(SEARCH, {}),
			):
				with self.subTest(route=name, site=site_id):
					self.assertEqual(
						self.client.get(reverse(name, kwargs={"site_id": site_id, **kwargs})).status_code,
						404,
					)

	def test_subject_and_category_outside_scope_or_unknown_are_404(self):
		self.assertEqual(
			self.client.get(self.subject_url(BY_SUBJECT, self.other_subject)).status_code, 404
		)
		self.assertEqual(
			self.client.get(self.url(BY_SUBJECT, subject_slug="nope")).status_code, 404
		)
		outside = TeamCategory.objects.create(
			team=self.team, category_name="Outside", category_slug="outside"
		)
		outside.subjects.add(self.other_subject)
		self.assertEqual(
			self.client.get(self.category_url(BY_CATEGORY, outside)).status_code, 404
		)
		self.assertEqual(
			self.client.get(self.url(BY_CATEGORY, category_slug="nope")).status_code, 404
		)

	def test_same_slug_in_two_teams_resolves_within_scope(self):
		other_team = type(self.team).objects.create(
			name="Other Team", organization=self.org, slug="other-team"
		)
		Subject.objects.create(
			subject_name="Twin", subject_slug=self.subject.subject_slug, team=other_team
		)
		self.make_patent("Mine")
		self.assertEqual(self.get_titles(self.subject_url(BY_SUBJECT)), ["Mine"])

	def test_family_of_another_subject_is_not_in_the_feed(self):
		self.make_patent("Other", subjects=[self.other_subject])
		self.assertEqual(self.get_titles(self.subject_url(BY_SUBJECT)), [])


class PatentFeedContentTests(PatentFixture, TestCase):
	def setUp(self):
		self.build()

	def subject_feed(self):
		response = self.client.get(self.subject_url(BY_SUBJECT))
		self.assertEqual(response.status_code, 200)
		return response

	# --- order, dates and size ---------------------------------------------

	def test_ordered_by_publication_not_discovery(self):
		"""A family discovered today but published in 2018 is not in the top 50
		once 50 newer families exist."""
		for i in range(51):
			self.make_patent(
				f"Family {i:02d}",
				published=date(2021, 1, 1) + timedelta(days=i),
				discovery=self.now - timedelta(days=400 + i),
			)
		self.make_patent("Old but just found", published=date(2018, 3, 1), discovery=self.now)
		titles = self.get_titles(self.subject_url(BY_SUBJECT))
		self.assertEqual(len(titles), 50)
		self.assertNotIn("Old but just found", titles)
		# Newest publication first; the oldest of the 51 is cut off instead.
		self.assertEqual(titles[0], "Family 50")
		self.assertNotIn("Family 00", titles)

	def test_publication_date_ties_break_on_newest_patent_id(self):
		first = self.make_patent("First", published=date(2022, 1, 1))
		second = self.make_patent("Second", published=date(2022, 1, 1))
		self.assertGreater(second.pk, first.pk)
		self.assertEqual(self.get_titles(self.subject_url(BY_SUBJECT)), ["Second", "First"])

	def test_null_publication_date_is_left_out(self):
		self.make_patent("Dated")
		self.make_patent("Undated", published=None)
		self.assertEqual(self.get_titles(self.subject_url(BY_SUBJECT)), ["Dated"])

	def test_pubdate_is_the_publication_date_at_midnight_utc(self):
		self.make_patent("Dated", published=date(2021, 3, 3), discovery=self.now)
		pub = self.items(self.subject_feed())[0].findtext("pubDate")
		self.assertEqual(
			parsedate_to_datetime(pub), datetime(2021, 3, 3, 0, 0, tzinfo=dt_timezone.utc)
		)

	# --- licence: processed records only ------------------------------------

	def assert_no_raw_ops_text(self, response):
		body = response.content.decode()
		for marker in RAW_MARKERS:
			self.assertNotIn(marker, body)

	def test_no_raw_ops_text_in_any_feed(self):
		patent = self.make_patent(
			"Remyelination device",
			category=self.category,
			sponsors=[self.sponsor],
			has_grant=True,
			earliest_priority_date=date(2020, 5, 5),
		)
		PatentSiteContent.objects.create(
			patent=patent, site=self.site, takeaways="Editor says so", summary_plain_english="Plain"
		)
		urls = {
			"subject": self.subject_url(BY_SUBJECT),
			"category": self.category_url(BY_CATEGORY),
			# search= matches the abstract server-side; filtering is fine, the
			# text itself must never be output.
			"search": self.url(SEARCH) + "?search=remyelination",
			"search (no params)": self.url(SEARCH),
		}
		for name, url in urls.items():
			with self.subTest(feed=name):
				response = self.client.get(url)
				self.assertEqual(response.status_code, 200)
				self.assertEqual(self.titles(response), ["Remyelination device"])
				self.assert_no_raw_ops_text(response)

	def test_raw_columns_are_not_even_loaded(self):
		self.make_patent("One")
		with CaptureQueriesContext(connection) as ctx:
			self.client.get(self.subject_url(BY_SUBJECT))
		patent_selects = [
			q["sql"] for q in ctx if 'FROM "patents"' in q["sql"] and 'WHERE "patents"."patent_id" IN' in q["sql"]
		]
		self.assertTrue(patent_selects)
		for sql in patent_selects:
			for column in ('"patents"."summary"', '"patents"."claims"', '"patents"."inventors"'):
				self.assertNotIn(column, sql)

	# --- item content ----------------------------------------------------------

	def test_link_is_espacenet_and_description_repeats_it(self):
		patent = self.make_patent("Linked")
		response = self.subject_feed()
		item = self.items(response)[0]
		self.assertEqual(item.findtext("link"), patent.link)
		description = item.findtext("description")
		self.assertIn("View on Espacenet", description)
		self.assertIn("worldwide.espacenet.com", description)

	def test_guid_is_not_a_permalink(self):
		patent = self.make_patent("Guid")
		guid = self.items(self.subject_feed())[0].find("guid")
		self.assertEqual(guid.text, f"urn:gregory:patent:{patent.pk}")
		self.assertEqual(guid.get("isPermaLink"), "false")

	def test_facts_row_and_grant_wording(self):
		self.make_patent(
			"With grant",
			sponsors=[self.sponsor, self.other_sponsor],
			has_grant=True,
			earliest_priority_date=date(2020, 5, 5),
			published=date(2021, 11, 25),
		)
		self.make_patent("No grant", published=date(2021, 1, 1))
		response = self.subject_feed()
		with_grant, without = self.description(response, 0), self.description(response, 1)
		self.assertIn("Acme Pharma, Beta Bio", with_grant)
		self.assertIn("Priority 2020-05-05", with_grant)
		self.assertIn("First published 2021-11-25", with_grant)
		self.assertIn("Grant published", with_grant)
		self.assertNotIn("Granted", with_grant)
		self.assertNotIn("Grant", without)
		self.assertNotIn("Priority", without)

	def test_creator_lists_sponsors_only(self):
		patent = self.make_patent("Mixed", sponsors=[self.sponsor])
		# An applicant that didn't resolve to a sponsor, and an individual,
		# are not creators.
		PatentApplicant.objects.create(
			patent=patent, sponsor=None, raw_name="UNRESOLVED CO LTD", sequence=5
		)
		PatentApplicant.objects.create(
			patent=patent,
			sponsor=self.other_sponsor,
			raw_name="Beta Bio person",
			sequence=6,
			is_individual=True,
		)
		response = self.subject_feed()
		self.assertEqual(self.creators(response), "Acme Pharma")
		body = response.content.decode()
		self.assertNotIn("Beta Bio", body)
		self.assertNotIn("UNRESOLVED CO LTD", body)

	def test_creator_is_first_three_then_et_al(self):
		sponsors = [
			Sponsor.objects.create(name=f"Sponsor {i}", slug=f"sponsor-{i}") for i in range(4)
		]
		self.make_patent("Crowded", sponsors=sponsors)
		self.assertEqual(self.creators(self.subject_feed()), "Sponsor 0, Sponsor 1, Sponsor 2 et al.")

	def test_no_creator_without_sponsors(self):
		self.make_patent("Nobody")
		self.assertIsNone(self.creators(self.subject_feed()))

	def test_categories_are_in_scope_subject_and_category_names(self):
		self.make_patent("Cat", subjects=[self.subject, self.other_subject], category=self.category)
		categories = [c.text for c in self.items(self.subject_feed())[0].findall("category")]
		self.assertEqual(categories, ["Reader Subject", "Reader Category"])

	# --- editorial content ---------------------------------------------------------

	def test_this_sites_editorial_text_appears_and_is_escaped(self):
		patent = self.make_patent("Edited")
		other_site = Site.objects.create(domain="other.example.com", name="Other")
		PatentSiteContent.objects.create(
			patent=patent,
			site=self.site,
			takeaways="Takeaway <script>alert(1)</script>\nline two",
			summary_plain_english="Plain & simple",
		)
		PatentSiteContent.objects.create(
			patent=patent, site=other_site, takeaways="Other site takeaway"
		)
		description = self.description(self.subject_feed())
		self.assertIn("Plain &amp; simple", description)
		self.assertIn("&lt;script&gt;", description)
		self.assertNotIn("<script>", description)
		self.assertIn("<br>", description)
		self.assertNotIn("Other site takeaway", description)
		self.assertLess(description.index("In plain English"), description.index("Key takeaways"))

	# --- linked trials ----------------------------------------------------------

	def test_only_visible_category_links_are_listed(self):
		patent = self.make_patent("Linked", sponsors=[self.sponsor])
		shown = self.make_trial("Shown trial")
		self.link_trial(patent, shown)
		self.link_trial(patent, self.make_trial("Suppressed trial"), suppressed=True)
		self.link_trial(patent, self.make_trial("Subject basis trial"), basis=PatentTrialLink.BASIS_SUBJECT)
		self.link_trial(
			patent, self.make_trial("Out of scope trial", subjects=[self.other_subject])
		)
		description = self.description(self.subject_feed())
		self.assertIn("Shown trial", description)
		self.assertIn("Filed during trial", description)
		self.assertIn("(Acme Pharma)", description)
		for hidden in ("Suppressed trial", "Subject basis trial", "Out of scope trial"):
			self.assertNotIn(hidden, description)
		self.assertNotIn("more", description)

	def test_after_links_say_after(self):
		patent = self.make_patent("Linked")
		self.link_trial(patent, self.make_trial("Finished trial"), timing=PatentTrialLink.TIMING_AFTER)
		self.assertIn("Filed after trial: ", self.description(self.subject_feed()))

	def test_at_most_three_links_then_and_n_more(self):
		patent = self.make_patent("Many links")
		for i in range(5):
			self.link_trial(patent, self.make_trial(f"Trial number {i}"))
		description = self.description(self.subject_feed())
		self.assertEqual(description.count("Filed during trial"), 3)
		self.assertIn("and 2 more", description)

	def test_trial_link_target_follows_sitemap_include_trials(self):
		patent = self.make_patent("Linked")
		trial = self.make_trial("Shown trial")
		self.link_trial(patent, trial)
		description = self.description(self.subject_feed())
		self.assertIn(f'href="{trial.link}"', description)
		self.settings_row.sitemap_include_trials = True
		self.settings_row.save()
		cache.clear()
		description = self.description(self.subject_feed())
		self.assertIn(f'href="https://reader.example.com/trials/{trial.trial_id}/"', description)

	def test_trial_title_is_truncated_and_escaped(self):
		patent = self.make_patent("Linked")
		self.link_trial(patent, self.make_trial("<b>x</b> " + "long " * 60))
		description = self.description(self.subject_feed())
		self.assertIn("&lt;b&gt;x&lt;/b&gt;", description)
		self.assertNotIn("<b>", description)
		anchor_text = description.split('">', 1)[1].split("</a>", 1)[0]
		self.assertTrue(anchor_text.endswith("…"))
		self.assertLessEqual(len(anchor_text.replace("&lt;", "<").replace("&gt;", ">")), 120)

	def test_trial_with_a_non_http_link_is_not_linked(self):
		patent = self.make_patent("Linked")
		trial = self.make_trial("Weird link trial")
		type(trial).objects.filter(pk=trial.pk).update(link="javascript:alert(1)")
		self.link_trial(patent, trial)
		description = self.description(self.subject_feed())
		self.assertIn("Weird link trial", description)
		self.assertNotIn("javascript:", description)

	# --- category feed ------------------------------------------------------------

	def test_category_feed_lists_assigned_families(self):
		self.make_patent("In category", category=self.category)
		self.make_patent("Not in category")
		self.assertEqual(self.get_titles(self.category_url(BY_CATEGORY)), ["In category"])

	def test_category_does_not_leak_through_a_subject_the_site_does_not_cover(self):
		self.make_patent("Leaky", subjects=[self.other_subject], category=self.category)
		self.make_patent("Fine", subjects=[self.subject], category=self.category)
		self.assertEqual(self.get_titles(self.category_url(BY_CATEGORY)), ["Fine"])

	# --- channel ---------------------------------------------------------------------

	def test_channel_elements(self):
		self.make_patent("One")
		root = ET.fromstring(self.subject_feed().content)
		channel = root.find("./channel")
		self.assertEqual(channel.findtext("title"), "Reader Subject patents — Reader Site")
		self.assertEqual(channel.findtext("link"), "https://reader.example.com/")
		self.assertIn("European Patent Office", channel.findtext("description"))
		self.assertIn("first publication", channel.findtext("description"))
		self_link = [
			link.get("href") for link in channel.findall(f"{ATOM}link") if link.get("rel") == "self"
		]
		self.assertEqual(
			self_link, ["http://testserver" + self.subject_url(BY_SUBJECT)]
		)
		category_root = ET.fromstring(self.client.get(self.category_url(BY_CATEGORY)).content)
		self.assertEqual(
			category_root.findtext("./channel/title"), "Patents: Reader Category — Reader Site"
		)

	def test_cached_for_an_hour(self):
		self.make_patent("First")
		url = self.subject_url(BY_SUBJECT)
		self.assertEqual(self.get_titles(url), ["First"])
		self.make_patent("Second", published=date(2023, 1, 1))
		self.assertEqual(self.get_titles(url), ["First"])
		cache.clear()
		self.assertEqual(self.get_titles(url), ["Second", "First"])


class PatentSearchFeedTests(PatentFixture, TestCase):
	def setUp(self):
		self.build()

	def search(self, query="", expect=200):
		url = self.url(SEARCH)
		response = self.client.get(f"{url}?{query}" if query else url)
		self.assertEqual(response.status_code, expect, query)
		return response

	def search_titles(self, query=""):
		return self.titles(self.search(query))

	def test_no_parameters_is_the_whole_scope(self):
		self.make_patent("In scope")
		self.make_patent("Other subject", subjects=[self.other_subject])
		self.make_patent("Undated", published=None)
		self.assertEqual(self.search_titles(), ["In scope"])

	def test_ordered_by_publication_and_limited_to_fifty(self):
		for i in range(51):
			self.make_patent(f"Family {i:02d}", published=date(2021, 1, 1) + timedelta(days=i))
		self.make_patent("Old but just found", published=date(2018, 3, 1), discovery=self.now)
		titles = self.search_titles()
		self.assertEqual(len(titles), 50)
		self.assertEqual(titles[0], "Family 50")
		self.assertNotIn("Old but just found", titles)

	def test_text_filters_match_but_do_not_output_the_text(self):
		self.make_patent("Alpha device")
		self.make_patent("Beta device", raw_markers=False, summary="something else")
		self.assertEqual(self.search_titles("search=remyelination"), ["Alpha device"])
		self.assertEqual(self.search_titles("summary=remyelination"), ["Alpha device"])
		self.assertEqual(self.search_titles("title=beta"), ["Beta device"])

	def test_applicant_sponsor_slug(self):
		self.make_patent("Acme one", sponsors=[self.sponsor])
		self.make_patent("Beta one", sponsors=[self.other_sponsor])
		self.make_patent("Nobody")
		self.assertEqual(self.search_titles("applicant_sponsor_slug=acme-pharma"), ["Acme one"])
		self.assertEqual(self.search_titles("applicant_sponsor_slug=nope"), [])

	def test_has_grant(self):
		self.make_patent("Granted one", has_grant=True, published=date(2022, 2, 1))
		self.make_patent("Plain one", published=date(2022, 1, 1))
		self.assertEqual(self.search_titles("has_grant=true"), ["Granted one"])
		self.assertEqual(self.search_titles("has_grant=false"), ["Plain one"])

	def test_has_trial_link_counts_only_visible_category_links(self):
		linked = self.make_patent("Linked", published=date(2022, 5, 1))
		suppressed = self.make_patent("Suppressed only", published=date(2022, 4, 1))
		subject_basis = self.make_patent("Subject basis only", published=date(2022, 3, 1))
		out_of_scope = self.make_patent("Out of scope trial only", published=date(2022, 2, 1))
		self.make_patent("No links", published=date(2022, 1, 1))
		self.link_trial(linked, self.make_trial("T1"))
		self.link_trial(suppressed, self.make_trial("T2"), suppressed=True)
		self.link_trial(subject_basis, self.make_trial("T3"), basis=PatentTrialLink.BASIS_SUBJECT)
		self.link_trial(out_of_scope, self.make_trial("T4", subjects=[self.other_subject]))
		self.assertEqual(self.search_titles("has_trial_link=true"), ["Linked"])
		self.assertEqual(
			self.search_titles("has_trial_link=false"),
			["Suppressed only", "Subject basis only", "Out of scope trial only", "No links"],
		)

	def test_subject_and_category_parameters(self):
		self.make_patent("In category", category=self.category, published=date(2022, 2, 1))
		self.make_patent("Plain", published=date(2022, 1, 1))
		self.assertEqual(
			self.search_titles(f"category_slug={self.category.category_slug}"), ["In category"]
		)
		self.assertEqual(
			self.search_titles(f"subject_id={self.subject.pk}"), ["In category", "Plain"]
		)

	def test_scope_404s(self):
		self.search(f"subject_id={self.other_subject.pk}", expect=404)
		outside = TeamCategory.objects.create(
			team=self.team, category_name="Outside", category_slug="outside"
		)
		outside.subjects.add(self.other_subject)
		self.search("category_slug=outside", expect=404)
		self.search("category_slug=nope", expect=404)

	def test_invalid_values_are_400(self):
		self.search("subject_id=abc", expect=400)
		self.search("has_grant=maybe", expect=400)
		self.search("has_trial_link=2", expect=400)
		self.search("search=" + "x" * 301, expect=400)

	def test_boolean_values_are_case_insensitive(self):
		self.make_patent("Granted one", has_grant=True)
		self.assertEqual(self.search_titles("has_grant=TRUE"), ["Granted one"])

	def test_non_allowlisted_parameters_do_nothing(self):
		self.make_patent("Alpha", sponsors=[self.sponsor])
		self.make_patent("Beta", published=date(2023, 1, 1))
		baseline = self.search("has_grant=false")
		self.assertEqual(len(self.items(baseline)), 2)
		extras = (
			"claims=nothingmatchesthis",
			"cpc=ZZZ",
			"inventor=nobody",
			"applicant=nobody",
			"include_subject_links=true",
			"publication_date_after=2100-01-01",
			"ml_threshold=0.99",
			"utm_source=x",
			"publication_number=EP0",
		)
		for extra in extras:
			with self.subTest(extra=extra):
				# A cold cache, so the filter really ran with this parameter.
				cache.clear()
				response = self.search(f"has_grant=false&{extra}")
				self.assertEqual(response.content, baseline.content)

	def test_non_allowlisted_parameters_do_not_change_the_cache_key(self):
		self.make_patent("Alpha")
		self.search("has_grant=false")
		with CaptureQueriesContext(connection) as ctx:
			self.search("utm_source=x&has_grant=false&cpc=ZZZ")
		self.assertEqual(len(ctx), 0)

	def test_parameter_order_shares_one_cache_entry(self):
		self.make_patent("Alpha", has_grant=True)
		first = self.search("has_grant=true&search=remyelination")
		self.make_patent("Beta", published=date(2023, 1, 1), has_grant=True)
		second = self.search("search=remyelination&has_grant=true")
		self.assertEqual(first.content, second.content)

	def test_x_robots_tag_on_fresh_and_cached_responses(self):
		self.make_patent("Alpha")
		for _ in range(2):
			response = self.search()
			self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")

	def test_disabled_site_is_404(self):
		self.settings_row.patents_enabled = False
		self.settings_row.save()
		self.search(expect=404)

	def test_self_link_and_channel_title(self):
		sponsor_query = "applicant_sponsor_slug=acme-pharma&has_grant=true&search=remyelination&utm_source=x"
		root = ET.fromstring(self.search(sponsor_query).content)
		channel = root.find("./channel")
		self.assertEqual(
			channel.findtext("title"),
			'Patent search: "remyelination" · Acme Pharma · grant published — Reader Site',
		)
		self_link = [
			link.get("href") for link in channel.findall(f"{ATOM}link") if link.get("rel") == "self"
		][0]
		self.assertEqual(
			self_link,
			"http://testserver" + self.url(SEARCH)
			+ "?applicant_sponsor_slug=acme-pharma&has_grant=true&search=remyelination",
		)
		self.assertNotIn("utm_source", self_link)
		self.assertEqual(
			ET.fromstring(self.search().content).findtext("./channel/title"),
			"All patents — Reader Site",
		)
		linked = ET.fromstring(self.search("has_trial_link=false&has_grant=false").content)
		self.assertEqual(
			linked.findtext("./channel/title"),
			"Patent search: no grant published · no linked trial — Reader Site",
		)
		self.assertEqual(channel.findtext("link"), "https://reader.example.com/")

	def test_robots_txt_disallows_patent_search(self):
		self.assertIn(
			"Disallow: /feed/sites/*/patents/search/", self.client.get("/robots.txt").content.decode()
		)


class PatentFeedQueryCountTests(PatentFixture, TestCase):
	def setUp(self):
		self.build()
		# Two in-scope subjects, 3 families in one and 30 in the other, each
		# with applicants, editorial content and trial links.
		self.big = Subject.objects.create(
			subject_name="Big Subject",
			subject_slug="big-subject",
			team=self.team,
		)
		self.settings_row.scope_subjects.add(self.big)
		self.category.subjects.add(self.big)
		for subject, count in ((self.subject, 3), (self.big, 30)):
			for i in range(count):
				patent = self.make_patent(
					f"{subject.subject_slug}-{i}",
					subjects=[subject],
					category=self.category,
					published=date(2022, 1, 1) + timedelta(days=i),
					sponsors=[self.sponsor, self.other_sponsor],
					has_grant=True,
				)
				PatentSiteContent.objects.create(
					patent=patent, site=self.site, takeaways=f"t{i}", summary_plain_english=f"p{i}"
				)
				for j in range(2):
					self.link_trial(patent, self.make_trial(f"{subject.subject_slug}-{i}-trial-{j}", subjects=[subject]))

	def count_queries(self, url):
		# Warm per-process caches (Site.objects.get_current in Feed.get_feed)
		# so the first feed measured doesn't pay a one-off extra query.
		self.client.get(url)
		cache.clear()
		with CaptureQueriesContext(connection) as ctx:
			response = self.client.get(url)
		self.assertEqual(response.status_code, 200)
		return len(ctx), len(self.items(response))

	def test_subject_feed_is_flat(self):
		small_queries, small_items = self.count_queries(self.subject_url(BY_SUBJECT))
		big_queries, big_items = self.count_queries(
			self.url(BY_SUBJECT, subject_slug="big-subject")
		)
		self.assertEqual((small_items, big_items), (3, 30))
		self.assertEqual(small_queries, big_queries)

	def test_search_feed_is_flat(self):
		small_queries, small_items = self.count_queries(
			self.url(SEARCH) + f"?subject_id={self.subject.pk}"
		)
		big_queries, big_items = self.count_queries(self.url(SEARCH) + f"?subject_id={self.big.pk}")
		self.assertEqual((small_items, big_items), (3, 30))
		self.assertEqual(small_queries, big_queries)

	def test_category_feed_is_flat_as_it_grows(self):
		# The category spans both subjects: 33 items either way; compare with
		# a one-family category.
		small = TeamCategory.objects.create(team=self.team, category_name="Small", category_slug="small")
		small.subjects.add(self.subject)
		PatentCategoryAssignment.objects.create(
			patents=Patents.objects.filter(subjects=self.subject).first(), teamcategory=small
		)
		small_queries, small_items = self.count_queries(self.category_url(BY_CATEGORY, small))
		big_queries, big_items = self.count_queries(self.category_url(BY_CATEGORY))
		self.assertEqual((small_items, big_items), (1, 33))
		self.assertEqual(small_queries, big_queries)
