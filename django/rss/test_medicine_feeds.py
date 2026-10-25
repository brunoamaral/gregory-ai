"""
EMA medicine event feeds (feed/sites/<site_id>/medicines/ and
feed/sites/<site_id>/medicines/category/<slug>/).

Reuses the fixtures of the reader-feed tests. Every class clears the cache in
setUp: both feeds are cache_page'd in the default cache (a LocMemCache under
test). Nothing here calls EMA: medicines and events are built directly.
"""

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone as dt_timezone
from email.utils import parsedate_to_datetime

from django.contrib.sites.models import Site
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from gregory.models import (
	CategoryAssignmentSource,
	Medicine,
	MedicineCategoryAssignment,
	MedicineEvent,
	Subject,
	TeamCategory,
)
from rss.feed_scope import RECONSTRUCTED_EVENT_DAYS
from rss.medicine_feeds import MEDICINE_FEED_LIMIT
from rss.test_reader_feeds import FeedFixture

ATOM = "{http://www.w3.org/2005/Atom}"
BY_CATEGORY = "site_medicines_by_category_feed"
SITE_FEED = "site_medicines_feed"

Kind = MedicineEvent.Kind
Basis = MedicineEvent.Basis


class MedicineFixture(FeedFixture):
	def build(self):
		self.build_fixture()
		self.settings_row.medicines_enabled = True
		self.settings_row.save()
		self.today = timezone.localdate()
		self.counter = 0

	def make_medicine(
		self,
		name,
		inn="ocrelizumab",
		category="default",
		suppressed=False,
		source=CategoryAssignmentSource.AUTOMATIC,
		**extra,
	):
		self.counter += 1
		fields = {
			"ema_product_number": f"EMEA/H/C/{900000 + self.counter}",
			"name": name,
			"inn": inn,
			"status": "Authorised",
			"indication": f"Indication of {name}.",
			"url": f"https://www.ema.europa.eu/en/medicines/human/EPAR/{name.lower().replace(' ', '-')}",
		}
		fields.update(extra)
		medicine = Medicine.objects.create(**fields)
		category = self.category if category == "default" else category
		if category is not None:
			self.link(medicine, category, suppressed=suppressed, source=source)
		return medicine

	def link(self, medicine, category, suppressed=False, source=CategoryAssignmentSource.AUTOMATIC):
		return MedicineCategoryAssignment.objects.create(
			medicine=medicine, teamcategory=category, suppressed=suppressed, source=source
		)

	def make_event(self, medicine, kind=Kind.AUTHORISED, days_ago=1, basis=Basis.OBSERVED, **extra):
		return MedicineEvent.objects.create(
			medicine=medicine,
			kind=kind,
			event_date=self.today - timedelta(days=days_ago),
			basis=basis,
			**extra,
		)

	def category_feed(self, category=None):
		return self.client.get(self.category_url(BY_CATEGORY, category))

	def site_feed(self):
		return self.client.get(self.url(SITE_FEED))

	def routes(self):
		return [(BY_CATEGORY, self.category_url(BY_CATEGORY)), (SITE_FEED, self.url(SITE_FEED))]


class MedicineFeedScopeTests(MedicineFixture, TestCase):
	def setUp(self):
		self.build()

	def test_enabled_empty_feeds_are_200(self):
		for name, url in self.routes():
			with self.subTest(route=name):
				response = self.client.get(url)
				self.assertEqual(response.status_code, 200)
				self.assertEqual(self.items(response), [])

	def test_medicines_disabled_is_404_even_with_rss_enabled_and_category_in_scope(self):
		self.make_event(self.make_medicine("Ocrevus"))
		self.settings_row.medicines_enabled = False
		self.settings_row.save()
		for name, url in self.routes():
			with self.subTest(route=name):
				self.assertEqual(self.client.get(url).status_code, 404)

	def test_medicines_switch_defaults_to_off(self):
		site = Site.objects.create(domain="fresh.example.com", name="Fresh")
		from sitesettings.models import CustomSetting

		row = CustomSetting.objects.create(site=site, title="Fresh", rss_enabled=True)
		self.assertFalse(row.medicines_enabled)
		row.scope_subjects.add(self.subject)
		self.assertEqual(self.client.get(self.url(SITE_FEED, site_id=site.pk)).status_code, 404)

	def test_rss_disabled_is_404_even_with_medicines_enabled(self):
		self.settings_row.rss_enabled = False
		self.settings_row.save()
		for name, url in self.routes():
			with self.subTest(route=name):
				self.assertEqual(self.client.get(url).status_code, 404)

	def test_the_patents_switch_does_not_open_the_medicine_feeds(self):
		self.settings_row.medicines_enabled = False
		self.settings_row.patents_enabled = True
		self.settings_row.save()
		for name, url in self.routes():
			with self.subTest(route=name):
				self.assertEqual(self.client.get(url).status_code, 404)

	def test_unknown_site_and_site_without_settings_are_404(self):
		other = Site.objects.create(domain="nosettings.example.com", name="No settings")
		for name in (BY_CATEGORY, SITE_FEED):
			kwargs = {"category_slug": self.category.category_slug} if name == BY_CATEGORY else {}
			with self.subTest(route=name, case="no settings"):
				self.assertEqual(self.client.get(self.url(name, site_id=other.pk, **kwargs)).status_code, 404)
			with self.subTest(route=name, case="unknown site"):
				self.assertEqual(self.client.get(self.url(name, site_id=999999, **kwargs)).status_code, 404)

	def test_category_outside_scope_or_unknown_is_404(self):
		elsewhere = TeamCategory.objects.create(
			team=self.team, category_name="Elsewhere", category_slug="elsewhere"
		)
		elsewhere.subjects.add(self.other_subject)
		self.make_event(self.make_medicine("Hidden", category=elsewhere))
		self.assertEqual(self.category_feed(elsewhere).status_code, 404)
		self.assertEqual(
			self.client.get(self.url(BY_CATEGORY, category_slug="no-such-category")).status_code, 404
		)

	def test_category_with_no_subjects_is_404(self):
		bare = TeamCategory.objects.create(team=self.team, category_name="Bare", category_slug="bare")
		self.assertEqual(self.category_feed(bare).status_code, 404)


class MedicineFeedContentTests(MedicineFixture, TestCase):
	def setUp(self):
		self.build()
		self.medicine = self.make_medicine(
			"Ocrevus", inn="ocrelizumab", indication="Treatment of multiple sclerosis."
		)

	def category_titles(self, category=None):
		return self.titles(self.category_feed(category))

	# --- which events ----------------------------------------------------------------

	def test_an_observed_event_appears_with_title_link_and_guid(self):
		event = self.make_event(self.medicine, Kind.EC_DECISION, days_ago=3, basis=Basis.OBSERVED)
		for response in (self.category_feed(), self.site_feed()):
			(item,) = self.items(response)
			self.assertEqual(
				item.findtext("title"),
				"Ocrevus (ocrelizumab): New European Commission decision",
			)
			self.assertEqual(item.findtext("link"), self.medicine.url)
			guid = item.find("guid")
			self.assertEqual(guid.text, f"ema-medicine-event-{event.pk}")
			self.assertEqual(guid.get("isPermaLink"), "false")

	def test_description_is_the_indication_then_the_source(self):
		self.make_event(self.medicine)
		description = self.items(self.category_feed())[0].findtext("description")
		self.assertEqual(
			description,
			"<p>Treatment of multiple sclerosis.</p><p>Source: European Medicines Agency</p>",
		)

	def test_description_escapes_the_indication(self):
		medicine = self.make_medicine("Odd", indication='<script>alert("x")</script> & more')
		self.make_event(medicine)
		description = self.items(self.category_feed())[0].findtext("description")
		self.assertNotIn("<script>", description)
		self.assertIn("&lt;script&gt;", description)
		self.assertTrue(description.endswith("Source: European Medicines Agency</p>"))

	def test_a_medicine_without_an_indication_still_carries_the_source(self):
		medicine = self.make_medicine("Bare", indication="")
		self.make_event(medicine)
		item = [i for i in self.items(self.category_feed()) if i.findtext("title").startswith("Bare")][0]
		self.assertEqual(item.findtext("description"), "<p>Source: European Medicines Agency</p>")

	def test_pubdate_is_the_event_date_at_midnight_utc(self):
		event = self.make_event(self.medicine, days_ago=5)
		pub = parsedate_to_datetime(self.items(self.category_feed())[0].findtext("pubDate"))
		self.assertEqual(
			pub, datetime.combine(event.event_date, datetime.min.time(), tzinfo=dt_timezone.utc)
		)

	def test_title_falls_back_to_the_active_substance_then_to_the_name(self):
		no_inn = self.make_medicine(
			"Hydrocortisone Aguettant", inn="", active_substance="hydrocortisone"
		)
		nothing = self.make_medicine("Mystery", inn="", active_substance="")
		self.make_event(no_inn, days_ago=1)
		self.make_event(nothing, days_ago=2)
		self.assertEqual(
			self.category_titles(),
			[
				"Hydrocortisone Aguettant (hydrocortisone): Authorised in the EU",
				"Mystery: Authorised in the EU",
			],
		)

	def test_every_kind_has_plain_english_wording(self):
		for kind in Kind.values:
			with self.subTest(kind=kind):
				self.assertIn(kind, MedicineEvent.IN_WORDS)
				self.assertTrue(MedicineEvent.IN_WORDS[kind])
		self.assertEqual(MedicineEvent.IN_WORDS[Kind.OPINION_POSITIVE], "CHMP positive opinion")
		self.assertEqual(MedicineEvent.IN_WORDS[Kind.AUTHORISED], "Authorised in the EU")
		# A kind added later without wording yet falls back to its own name.
		self.assertEqual(MedicineEvent(kind="something_new").in_words, "something_new")

	def test_suppressed_links_never_appear(self):
		hidden = self.make_medicine("Suppressed", suppressed=True)
		self.make_event(hidden)
		self.make_event(self.medicine)
		for response in (self.category_feed(), self.site_feed()):
			self.assertEqual(self.titles(response), ["Ocrevus (ocrelizumab): Authorised in the EU"])

	def test_a_medicine_suppressed_in_one_category_stays_through_another(self):
		other = TeamCategory.objects.create(team=self.team, category_name="Other", category_slug="other")
		other.subjects.add(self.subject)
		self.link(self.medicine, other, suppressed=True)
		self.make_event(self.medicine)
		self.assertEqual(len(self.items(self.category_feed(other))), 0)
		self.assertEqual(len(self.items(self.category_feed())), 1)

	def test_manual_links_appear(self):
		manual = self.make_medicine("Manual", source=CategoryAssignmentSource.MANUAL)
		self.make_event(manual)
		self.assertIn("Manual (ocrelizumab): Authorised in the EU", self.category_titles())

	def test_a_medicine_with_no_link_never_appears(self):
		self.make_event(self.make_medicine("Unlinked", category=None))
		self.assertEqual(self.items(self.site_feed()), [])

	def test_generics_and_biosimilars_never_appear(self):
		for name, flag in (("Generic", "generic"), ("Biosimilar", "biosimilar")):
			self.make_event(self.make_medicine(name, **{flag: True}))
		self.make_event(self.medicine)
		for response in (self.category_feed(), self.site_feed()):
			self.assertEqual(self.titles(response), ["Ocrevus (ocrelizumab): Authorised in the EU"])

	def test_old_reconstructed_events_do_not_appear_but_observed_ones_do(self):
		old = RECONSTRUCTED_EVENT_DAYS + 1
		self.make_event(self.medicine, Kind.OPINION_POSITIVE, days_ago=old, basis=Basis.RECONSTRUCTED)
		self.make_event(self.medicine, Kind.REFUSED, days_ago=old, basis=Basis.OBSERVED)
		self.make_event(
			self.medicine, Kind.AUTHORISED, days_ago=RECONSTRUCTED_EVENT_DAYS, basis=Basis.RECONSTRUCTED
		)
		self.make_event(self.medicine, Kind.EC_DECISION, days_ago=2, basis=Basis.RECONSTRUCTED)
		for response in (self.category_feed(), self.site_feed()):
			words = [t.split(": ", 1)[1] for t in self.titles(response)]
			self.assertEqual(
				words,
				["New European Commission decision", "Authorised in the EU", "Authorisation refused"],
			)

	def test_ordered_by_event_date_then_newest_event_id(self):
		oldest = self.make_event(self.medicine, Kind.OPINION_POSITIVE, days_ago=10)
		first_tie = self.make_event(self.medicine, Kind.AUTHORISED, days_ago=3)
		second_tie = self.make_event(self.make_medicine("Second"), Kind.AUTHORISED, days_ago=3)
		self.assertLess(first_tie.pk, second_tie.pk)
		self.assertLess(oldest.pk, first_tie.pk)
		for response in (self.category_feed(), self.site_feed()):
			guids = [i.findtext("guid") for i in self.items(response)]
			self.assertEqual(
				guids,
				[f"ema-medicine-event-{e.pk}" for e in (second_tie, first_tie, oldest)],
			)

	def test_limited_to_fifty_items(self):
		for i in range(MEDICINE_FEED_LIMIT + 10):
			self.make_event(self.make_medicine(f"Bulk {i}"), days_ago=1 + i % 40)
		for response in (self.category_feed(), self.site_feed()):
			self.assertEqual(len(self.items(response)), MEDICINE_FEED_LIMIT)

	def test_site_feed_lists_a_medicine_in_two_categories_once(self):
		other = TeamCategory.objects.create(team=self.team, category_name="Other", category_slug="other")
		other.subjects.add(self.subject)
		self.link(self.medicine, other)
		self.make_event(self.medicine)
		self.assertEqual(len(self.items(self.site_feed())), 1)

	def test_site_feed_covers_every_category_in_scope_and_no_other(self):
		other = TeamCategory.objects.create(team=self.team, category_name="Other", category_slug="other")
		other.subjects.add(self.subject)
		elsewhere = TeamCategory.objects.create(
			team=self.team, category_name="Elsewhere", category_slug="elsewhere"
		)
		elsewhere.subjects.add(self.other_subject)
		self.make_event(self.medicine, days_ago=1)
		self.make_event(self.make_medicine("In Other", category=other), days_ago=2)
		self.make_event(self.make_medicine("Out Of Scope", category=elsewhere), days_ago=3)
		self.assertEqual(
			[t.split(" (")[0] for t in self.titles(self.site_feed())], ["Ocrevus", "In Other"]
		)
		# The category feed shows only its own medicines.
		self.assertEqual(
			[t.split(" (")[0] for t in self.category_titles(other)], ["In Other"]
		)

	def test_only_the_overlap_with_the_scope_counts(self):
		# The fixture category has one subject in scope and one outside it, and
		# is served through the first. Take that subject out of the scope and
		# the category has no overlap left: a 404, and nothing in the site feed.
		self.make_event(self.medicine)
		self.assertEqual(self.category_feed().status_code, 200)
		self.assertEqual(len(self.items(self.site_feed())), 1)
		self.settings_row.scope_subjects.remove(self.subject)
		self.settings_row.scope_subjects.add(
			Subject.objects.create(subject_name="Unrelated", subject_slug="unrelated", team=self.team)
		)
		cache.clear()
		self.assertEqual(self.category_feed().status_code, 404)
		self.assertEqual(self.items(self.site_feed()), [])

	# --- channel ---------------------------------------------------------------------

	def test_channel_elements(self):
		self.make_event(self.medicine)
		root = ET.fromstring(self.category_feed().content)
		channel = root.find("./channel")
		self.assertEqual(channel.findtext("title"), "Medicines: Reader Category — Reader Site")
		self.assertEqual(channel.findtext("link"), "https://reader.example.com/")
		self.assertIn("European Medicines Agency", channel.findtext("description"))
		self_link = [
			link.get("href") for link in channel.findall(f"{ATOM}link") if link.get("rel") == "self"
		]
		self.assertEqual(self_link, ["http://testserver" + self.category_url(BY_CATEGORY)])

		site_root = ET.fromstring(self.site_feed().content)
		self.assertEqual(site_root.findtext("./channel/title"), "Medicine approvals — Reader Site")
		self_link = [
			link.get("href")
			for link in site_root.findall(f"./channel/{ATOM}link")
			if link.get("rel") == "self"
		]
		self.assertEqual(self_link, ["http://testserver" + self.url(SITE_FEED)])

	def test_cached_for_an_hour(self):
		self.make_event(self.medicine, days_ago=2)
		url = self.category_url(BY_CATEGORY)
		self.assertEqual(len(self.get_titles(url)), 1)
		self.make_event(self.medicine, Kind.EC_DECISION, days_ago=1)
		self.assertEqual(len(self.get_titles(url)), 1)
		cache.clear()
		self.assertEqual(len(self.get_titles(url)), 2)


class MedicineFeedQueryCountTests(MedicineFixture, TestCase):
	"""One render costs the same number of queries whatever the number of items."""

	def setUp(self):
		self.build()
		self.big = TeamCategory.objects.create(team=self.team, category_name="Big", category_slug="big")
		self.big.subjects.add(self.subject)
		self.small = TeamCategory.objects.create(team=self.team, category_name="Small", category_slug="small")
		self.small.subjects.add(self.subject)
		one = self.make_medicine("Single", category=self.small)
		self.make_event(one)
		for i in range(30):
			medicine = self.make_medicine(f"Bulk {i}", category=self.big)
			# Two events each, and a second category link, so neither the events
			# nor the links can turn into a per-item query.
			self.make_event(medicine, Kind.OPINION_POSITIVE, days_ago=1 + i)
			self.make_event(medicine, Kind.AUTHORISED, days_ago=1 + i + 1)
			self.link(medicine, self.small if i % 2 else self.category)

	def count_queries(self, url):
		# Warm per-process caches (Site.objects.get_current in Feed.get_feed)
		# so the first feed measured doesn't pay a one-off extra query.
		self.client.get(url)
		cache.clear()
		with CaptureQueriesContext(connection) as ctx:
			response = self.client.get(url)
		self.assertEqual(response.status_code, 200)
		return len(ctx), len(self.items(response))

	def test_category_feed_is_flat_as_it_grows(self):
		# "Small" now holds 16 medicines' worth of events; use a category with one.
		lone = TeamCategory.objects.create(team=self.team, category_name="Lone", category_slug="lone")
		lone.subjects.add(self.subject)
		self.link(Medicine.objects.get(name="Single"), lone)
		small_queries, small_items = self.count_queries(self.category_url(BY_CATEGORY, lone))
		big_queries, big_items = self.count_queries(self.category_url(BY_CATEGORY, self.big))
		self.assertEqual((small_items, big_items), (1, 50))
		self.assertEqual(small_queries, big_queries)

	def test_site_feed_is_flat_as_it_grows(self):
		site = Site.objects.create(domain="small.example.com", name="Small Site")
		from sitesettings.models import CustomSetting

		row = CustomSetting.objects.create(
			site=site, title="Small", rss_enabled=True, medicines_enabled=True
		)
		lone_subject = Subject.objects.create(
			subject_name="Lone", subject_slug="lone-subject", team=self.team
		)
		row.scope_subjects.add(lone_subject)
		lone = TeamCategory.objects.create(team=self.team, category_name="Lone", category_slug="lone")
		lone.subjects.add(lone_subject)
		self.link(self.make_medicine("Lonely", category=None), lone)
		self.make_event(Medicine.objects.get(name="Lonely"))

		small_queries, small_items = self.count_queries(self.url(SITE_FEED, site_id=site.pk))
		big_queries, big_items = self.count_queries(self.url(SITE_FEED))
		self.assertEqual((small_items, big_items), (1, 50))
		self.assertEqual(small_queries, big_queries)

	def test_the_count_is_small_and_fixed(self):
		queries, items = self.count_queries(self.category_url(BY_CATEGORY, self.big))
		self.assertEqual(items, 50)
		# The site, its settings row, the scope subjects, the category, its
		# subjects, the page of ids, and the events hydrated with their medicines.
		self.assertEqual(queries, 7)
		site_queries, site_items = self.count_queries(self.url(SITE_FEED))
		self.assertEqual(site_items, 50)
		# The same, with one query for every category in the scope instead of two
		# for the one category.
		self.assertEqual(site_queries, 6)
