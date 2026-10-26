"""
Medicines EMA approved for a subject's condition, with or without a category:
``MedicineSubjectLink`` as a second route to visibility in /medicines/ and
/medicines/events/, the ``tracked`` filter, the ``subjects`` / ``grouped_under`` /
``grouped_products`` fields, and the ``medicines_count`` on /categories/.

Built on the fixture of test_medicines_api.py (see its docstring for the sites,
subjects and categories). Added here:

  - ``untracked``: a subject link to S_ms, no category
  - ``both``: a live category link (C_ms) and a subject link (S_ms)
  - ``suppressed_subject``: only a suppressed subject link to S_ms
  - ``suppressed_subject_cat``: a suppressed subject link to S_ms and a live link to C_ms
  - ``untracked_als``: a subject link to S_als, no category
  - ``untracked_other``: a subject link to S_other, outside site ON's scope
  - ``hidden_cat_subject``: a subject link to S_ms and a link only to C_other (a category
    the caller cannot see)
  - ``lead`` and ``copy``: ``copy.grouped_under = lead``, both with a subject link to S_ms
    (``lead`` also has a live link to C_ms, ``copy`` to C_als)
"""

from datetime import timedelta

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from api.tests.test_medicines_api import (
	Basis,
	Kind,
	MedicineFixture,
	VISIBLE_BY_DEFAULT,
	_ids,
)
from gregory.models import Medicine, MedicineCategoryAssignment, MedicineSubjectLink


class SubjectLinkFixture(MedicineFixture):
	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		cls.untracked = cls._medicine("Untracked MS", "untrackedium")
		cls._subject_link(cls.untracked, cls.s_ms, matched_on="Multiple Sclerosis")
		cls.both = cls._medicine("Both routes", "bothium", cls.c_ms)
		cls._subject_link(cls.both, cls.s_ms)
		cls.suppressed_subject = cls._medicine("Suppressed subject", "suppsubjium")
		cls._subject_link(cls.suppressed_subject, cls.s_ms, suppressed=True)
		cls.suppressed_subject_cat = cls._medicine(
			"Suppressed subject with category", "suppsubjcatium", cls.c_ms
		)
		cls._subject_link(cls.suppressed_subject_cat, cls.s_ms, suppressed=True)
		cls.untracked_als = cls._medicine("Untracked ALS", "alsium")
		cls._subject_link(cls.untracked_als, cls.s_als, matched_on="Amyotrophic Lateral Sclerosis")
		cls.untracked_other = cls._medicine("Untracked other", "otherium")
		cls._subject_link(cls.untracked_other, cls.s_other)
		cls.hidden_cat_subject = cls._medicine("Hidden category", "hidcatium", cls.c_other)
		cls._subject_link(cls.hidden_cat_subject, cls.s_ms)
		cls.lead = cls._medicine("Lead product", "leadium", cls.c_ms)
		cls._subject_link(cls.lead, cls.s_ms)
		cls.copy = cls._medicine("Lead product copy", "leadium", cls.c_als, grouped_under=cls.lead)
		cls._subject_link(cls.copy, cls.s_ms)

		cls.ev_untracked = cls._event(cls.untracked, Kind.AUTHORISED, cls.today, Basis.OBSERVED)
		cls.ev_suppressed_subject = cls._event(
			cls.suppressed_subject, Kind.AUTHORISED, cls.today, Basis.OBSERVED
		)
		cls.ev_untracked_other = cls._event(
			cls.untracked_other, Kind.AUTHORISED, cls.today, Basis.OBSERVED
		)
		cls.ev_copy = cls._event(cls.copy, Kind.AUTHORISED, cls.today, Basis.OBSERVED)
		cls.ev_both = cls._event(cls.both, Kind.EC_DECISION, cls.today, Basis.OBSERVED)

	@classmethod
	def _subject_link(cls, medicine, subject, suppressed=False, matched_on=""):
		return MedicineSubjectLink.objects.create(
			medicine=medicine, subject=subject, suppressed=suppressed, matched_on=matched_on
		)

	def get(self, path, query="", **extra):
		if "page_size" not in query:
			query = f"{query}&page_size=100" if query else "page_size=100"
		response = self.client.get(f"{path}?{query}", **extra)
		self.assertEqual(response.status_code, 200, response.content)
		return response

	def meds(self, query="", **extra):
		return self.names(self.get("/medicines/", query, **extra))

	def event_ids(self, query="", **extra):
		return sorted(_ids(self.get("/medicines/events/", query, **extra)))


# What site ON lists by default now: the category-linked ones, plus the ones reached by a
# subject link alone, minus grouped copies, generics and biosimilars.
SUBJECT_ONLY = ["Untracked ALS", "Untracked MS"]
NEW_BY_DEFAULT = sorted(
	SUBJECT_ONLY
	+ [
		"Both routes",
		"Suppressed subject with category",
		"Hidden category",
		"Lead product",
	]
)
ALL_BY_DEFAULT = sorted(VISIBLE_BY_DEFAULT + NEW_BY_DEFAULT)


class SubjectLinkVisibilityTests(SubjectLinkFixture):
	def test_a_medicine_with_only_a_subject_link_is_listed(self):
		names = self.meds()
		for name in SUBJECT_ONLY:
			self.assertIn(name, names)
		self.assertEqual(names, ALL_BY_DEFAULT)

	def test_a_medicine_with_only_a_category_link_is_still_listed(self):
		self.assertIn("Ocrevus", self.meds())

	def test_a_medicine_with_both_routes_is_listed_once(self):
		response = self.get("/medicines/", "include_generics=true")
		self.assertEqual(_ids(response).count(self.both.pk), 1)
		ids = _ids(response)
		self.assertEqual(len(ids), len(set(ids)))
		self.assertEqual(response.data["count"], len(ids))

	def test_a_suppressed_subject_link_alone_does_not_make_a_medicine_visible(self):
		self.assertNotIn("Suppressed subject", self.meds("include_generics=true"))
		self.assertEqual(self.client.get(f"/medicines/{self.suppressed_subject.pk}/").status_code, 404)

	def test_a_suppressed_subject_link_with_a_live_category_link_stays_visible(self):
		self.assertIn("Suppressed subject with category", self.meds())
		self.assertEqual(
			self.client.get(f"/medicines/{self.suppressed_subject_cat.pk}/").status_code, 200
		)

	def test_a_suppressed_category_link_with_a_live_subject_link_stays_visible(self):
		MedicineCategoryAssignment.objects.filter(medicine=self.both).update(suppressed=True)
		self.assertIn("Both routes", self.meds())

	def test_a_subject_link_to_a_subject_outside_the_scope_is_not_visible(self):
		self.assertNotIn("Untracked other", self.meds("include_generics=true"))
		self.assertEqual(self.client.get(f"/medicines/{self.untracked_other.pk}/").status_code, 404)

	def test_detail_of_an_untracked_medicine(self):
		response = self.get(f"/medicines/{self.untracked.pk}/")
		self.assertFalse(response.data["tracked"])
		self.assertEqual(response.data["categories"], [])
		self.assertEqual([s["subject_slug"] for s in response.data["subjects"]], ["ms"])

	def test_a_site_with_the_switch_off_sees_nothing_through_subject_links_either(self):
		# S_ms is in site OFF's scope and `untracked` has a subject link to it, but the
		# site does not show medicines.
		key = self.key_for(self.site_off)
		response = self.get("/medicines/", HTTP_AUTHORIZATION=key.api_key)
		self.assertEqual(response.data["results"], [])
		self.assertEqual(
			self.client.get(f"/medicines/{self.untracked.pk}/", HTTP_AUTHORIZATION=key.api_key).status_code,
			404,
		)
		self.assertEqual(self.get("/medicines/events/", HTTP_AUTHORIZATION=key.api_key).data["results"], [])

	def test_events_follow_the_same_rule_through_their_medicine(self):
		ids = self.event_ids()
		self.assertIn(self.ev_untracked.pk, ids)
		self.assertIn(self.ev_both.pk, ids)
		self.assertNotIn(self.ev_suppressed_subject.pk, ids)
		self.assertNotIn(self.ev_untracked_other.pk, ids)

	def test_events_of_a_medicine_with_both_routes_are_not_repeated(self):
		ids = _ids(self.get("/medicines/events/", "page_size=100"))
		self.assertEqual(len(ids), len(set(ids)))


class SubjectLinkFilterTests(SubjectLinkFixture):
	def test_subject_id_alone_accepts_either_route(self):
		names = self.meds(f"subject_id={self.s_ms.pk}")
		self.assertIn("Untracked MS", names)  # subject link only
		self.assertIn("Ocrevus", names)  # category link only
		self.assertIn("Both routes", names)
		self.assertNotIn("Untracked ALS", names)
		# And once only, however many links match.
		ids = _ids(self.get("/medicines/", f"subject_id={self.s_ms.pk}"))
		self.assertEqual(len(ids), len(set(ids)))

	def test_subject_id_does_not_count_a_suppressed_subject_link(self):
		self.assertNotIn("Suppressed subject", self.meds(f"subject_id={self.s_ms.pk}&include_generics=true"))

	def test_a_medicine_with_a_suppressed_subject_link_and_a_live_category_link_matches_through_the_category(self):
		self.assertIn("Suppressed subject with category", self.meds(f"subject_id={self.s_ms.pk}"))

	def test_subject_id_with_category_id_keeps_the_single_link_rule(self):
		names = self.meds(f"subject_id={self.s_ms.pk}&category_id={self.c_ms.pk}")
		# Category links only: the medicine with just a subject link is not in the result.
		self.assertNotIn("Untracked MS", names)
		self.assertIn("Ocrevus", names)
		self.assertIn("Both routes", names)
		# The link has to be to category C_als and C_als has to have subject S_ms.
		self.assertEqual(self.meds(f"subject_id={self.s_ms.pk}&category_id={self.c_als.pk}"), [])

	def test_subject_id_with_category_slug_uses_category_links_only(self):
		names = self.meds(f"subject_id={self.s_ms.pk}&category_slug=ocrelizumab")
		self.assertNotIn("Untracked MS", names)
		self.assertIn("Ocrevus", names)

	def test_a_category_filter_alone_ignores_subject_links(self):
		self.assertNotIn("Untracked MS", self.meds(f"category_id={self.c_ms.pk}"))

	def test_site_id_accepts_either_route(self):
		names = self.meds(f"site_id={self.site_on.pk}")
		self.assertEqual(names, ALL_BY_DEFAULT)
		self.assertIn("Untracked MS", names)
		self.assertNotIn("Untracked other", names)

	def test_site_id_with_a_category_filter_uses_category_links_only(self):
		names = self.meds(f"site_id={self.site_on.pk}&category_id={self.c_ms.pk}")
		self.assertNotIn("Untracked MS", names)
		self.assertIn("Ocrevus", names)

	def test_site_id_with_medicines_off_returns_nothing_even_with_subject_links(self):
		self.assertEqual(self.meds(f"site_id={self.site_off.pk}"), [])
		self.assertEqual(self.meds(f"site_id={self.site_off.pk}&subject_id={self.s_ms.pk}"), [])
		self.assertEqual(self.event_ids(f"site_id={self.site_off.pk}"), [])

	def test_subject_id_and_site_id_must_both_hold_on_the_same_subject_link(self):
		# S_als is in site ON's scope: untracked ALS matches. S_other is not, so the
		# site condition fails on the one subject link that satisfies subject_id.
		self.assertEqual(
			self.meds(f"subject_id={self.s_als.pk}&site_id={self.site_on.pk}&tracked=false"),
			["Untracked ALS"],
		)
		self.assertEqual(self.meds(f"subject_id={self.s_other.pk}&site_id={self.site_on.pk}"), [])

	def test_a_subject_outside_the_callers_scope_reveals_nothing(self):
		self.assertEqual(self.meds(f"subject_id={self.s_other.pk}"), [])
		self.assertEqual(self.event_ids(f"subject_id={self.s_other.pk}"), [])

	def test_events_subject_id_accepts_either_route(self):
		ids = self.event_ids(f"subject_id={self.s_ms.pk}")
		self.assertIn(self.ev_untracked.pk, ids)
		self.assertIn(self.ev_new.pk, ids)
		self.assertNotIn(self.ev_untracked_other.pk, ids)
		self.assertNotIn(self.ev_untracked.pk, self.event_ids(f"subject_id={self.s_ms.pk}&category_id={self.c_ms.pk}"))

	def test_events_site_id_accepts_either_route(self):
		self.assertIn(self.ev_untracked.pk, self.event_ids(f"site_id={self.site_on.pk}"))


class TrackedFilterTests(SubjectLinkFixture):
	def test_tracked_false_lists_medicines_without_a_visible_category(self):
		names = self.meds("tracked=false")
		self.assertEqual(names, sorted(SUBJECT_ONLY + ["Hidden category"]))

	def test_tracked_true_lists_medicines_filed_under_a_visible_category(self):
		names = self.meds("tracked=true")
		self.assertEqual(names, sorted(VISIBLE_BY_DEFAULT + ["Both routes", "Suppressed subject with category", "Lead product"]))

	def test_tracked_is_exactly_categories_not_empty(self):
		for value, expected in (("true", True), ("false", False)):
			rows = self.get("/medicines/", f"tracked={value}&page_size=100&include_generics=true").data["results"]
			self.assertTrue(rows)
			for row in rows:
				with self.subTest(medicine=row["name"], tracked=value):
					self.assertEqual(bool(row["categories"]), expected)
					self.assertEqual(row["tracked"], expected)

	def test_a_category_the_caller_cannot_see_does_not_make_a_medicine_tracked(self):
		# `hidden_cat_subject` is filed only under C_other (S_other, outside site ON's
		# scope) and has a subject link to S_ms: visible, but untracked for this caller.
		row = next(
			r for r in self.get("/medicines/", "page_size=100").data["results"]
			if r["name"] == "Hidden category"
		)
		self.assertFalse(row["tracked"])
		self.assertEqual(row["categories"], [])
		self.assertIn("Hidden category", self.meds("tracked=false"))
		self.assertNotIn("Hidden category", self.meds("tracked=true"))

	def test_a_suppressed_category_link_does_not_make_a_medicine_tracked(self):
		MedicineCategoryAssignment.objects.filter(medicine=self.both).update(suppressed=True)
		self.assertIn("Both routes", self.meds("tracked=false"))

	def test_tracked_combines_with_subject_id(self):
		self.assertEqual(
			self.meds(f"subject_id={self.s_ms.pk}&tracked=false"), ["Hidden category", "Untracked MS"]
		)

	def test_tracked_on_events(self):
		untracked = self.event_ids("tracked=false")
		self.assertEqual(untracked, [self.ev_untracked.pk])
		tracked = self.event_ids("tracked=true")
		self.assertNotIn(self.ev_untracked.pk, tracked)
		self.assertIn(self.ev_both.pk, tracked)
		self.assertIn(self.ev_new.pk, tracked)

	def test_tracked_has_its_own_cached_count(self):
		# CachedCountMixin keys the cached COUNT(*) on the whole query string except
		# paging and ordering, so `tracked` is part of the key. A plain client keeps the
		# cache between requests.
		client = APIClient()
		cache.clear()
		untracked = client.get("/medicines/?tracked=false").data["count"]
		tracked = client.get("/medicines/?tracked=true").data["count"]
		everything = client.get("/medicines/").data["count"]
		self.assertEqual(untracked, 3)
		self.assertEqual(tracked + untracked, everything)
		# And now from the cache.
		self.assertEqual(client.get("/medicines/?tracked=false").data["count"], untracked)
		self.assertEqual(client.get("/medicines/?tracked=true").data["count"], tracked)
		events_untracked = client.get("/medicines/events/?tracked=false").data["count"]
		events_tracked = client.get("/medicines/events/?tracked=true").data["count"]
		self.assertEqual(events_untracked, 1)
		self.assertNotEqual(events_untracked, events_tracked)


class GroupingTests(SubjectLinkFixture):
	def test_grouped_copies_are_hidden_by_default(self):
		self.assertNotIn("Lead product copy", self.meds())
		self.assertIn("Lead product", self.meds())

	def test_include_generics_shows_grouped_copies_too(self):
		names = self.meds("include_generics=true")
		self.assertIn("Lead product copy", names)
		self.assertIn("Lead product", names)
		# And the generics and biosimilars it always showed.
		self.assertIn("Tyruko", names)

	def test_include_generics_false_is_the_default(self):
		self.assertEqual(self.meds("include_generics=false"), self.meds())

	def test_grouped_copies_are_hidden_from_the_link_filters_too(self):
		self.assertNotIn("Lead product copy", self.meds(f"category_id={self.c_als.pk}"))
		self.assertNotIn("Lead product copy", self.meds(f"subject_id={self.s_ms.pk}"))
		self.assertIn("Lead product copy", self.meds(f"category_id={self.c_als.pk}&include_generics=true"))

	def test_events_of_grouped_copies_are_hidden_by_default(self):
		self.assertNotIn(self.ev_copy.pk, self.event_ids())
		self.assertIn(self.ev_copy.pk, self.event_ids("include_generics=true"))

	def test_rows_carry_grouped_under(self):
		rows = {r["name"]: r for r in self.get("/medicines/", "include_generics=true&page_size=100").data["results"]}
		self.assertEqual(rows["Lead product copy"]["grouped_under"], self.lead.pk)
		self.assertIsNone(rows["Lead product"]["grouped_under"])
		self.assertNotIn("grouped_products", rows["Lead product"])

	def test_detail_of_a_lead_lists_its_copies(self):
		response = self.get(f"/medicines/{self.lead.pk}/")
		self.assertEqual(
			response.data["grouped_products"],
			[{"id": self.copy.pk, "name": "Lead product copy", "url": self.copy.url}],
		)
		self.assertIsNone(response.data["grouped_under"])

	def test_detail_of_a_copy_is_returned_and_points_at_its_lead(self):
		response = self.get(f"/medicines/{self.copy.pk}/")
		self.assertEqual(response.data["grouped_under"], self.lead.pk)
		self.assertEqual(response.data["grouped_products"], [])

	def test_a_copy_the_caller_cannot_see_is_not_listed_on_the_lead(self):
		MedicineCategoryAssignment.objects.filter(medicine=self.copy).delete()
		MedicineSubjectLink.objects.filter(medicine=self.copy).delete()
		self.assertEqual(self.get(f"/medicines/{self.lead.pk}/").data["grouped_products"], [])

	def test_copies_are_ordered_by_name(self):
		second = self._medicine("Another copy", "leadium", self.c_ms, grouped_under=self.lead)
		third = self._medicine("Zed copy", "leadium", self.c_ms, grouped_under=self.lead)
		names = [p["name"] for p in self.get(f"/medicines/{self.lead.pk}/").data["grouped_products"]]
		self.assertEqual(names, ["Another copy", "Lead product copy", "Zed copy"])
		self.assertEqual(len({second.pk, third.pk}), 2)


class SubjectsFieldTests(SubjectLinkFixture):
	def test_subjects_lists_the_visible_non_suppressed_links_by_name(self):
		medicine = self._medicine("Many subjects", "manyium")
		self._subject_link(medicine, self.s_ms, matched_on="Multiple Sclerosis")
		self._subject_link(medicine, self.s_als, matched_on="ALS")
		self._subject_link(medicine, self.s_other, matched_on="Other")  # outside the scope
		response = self.get(f"/medicines/{medicine.pk}/")
		self.assertEqual(
			response.data["subjects"],
			[
				{"id": self.s_als.pk, "subject_name": "ALS", "subject_slug": "als", "matched_on": "ALS"},
				{"id": self.s_ms.pk, "subject_name": "MS", "subject_slug": "ms", "matched_on": "Multiple Sclerosis"},
			],
		)

	def test_a_suppressed_subject_link_is_not_listed(self):
		response = self.get(f"/medicines/{self.suppressed_subject_cat.pk}/")
		self.assertEqual(response.data["subjects"], [])
		self.assertTrue(response.data["tracked"])

	def test_a_medicine_with_only_a_category_has_no_subjects(self):
		row = self.get(f"/medicines/{self.ocrevus.pk}/").data
		self.assertEqual(row["subjects"], [])
		self.assertTrue(row["tracked"])

	def test_the_list_row_has_the_new_fields(self):
		row = self.get("/medicines/", "name=Untracked%20MS").data["results"][0]
		self.assertFalse(row["tracked"])
		self.assertIsNone(row["grouped_under"])
		self.assertEqual(
			row["subjects"],
			[{"id": self.s_ms.pk, "subject_name": "MS", "subject_slug": "ms", "matched_on": "Multiple Sclerosis"}],
		)

	def test_the_serializer_falls_back_to_a_query_when_nothing_was_prefetched(self):
		from types import SimpleNamespace

		from api.serializers import MedicineSerializer

		request = SimpleNamespace(visible_medicine_subject_ids={self.s_ms.pk})
		data = MedicineSerializer(self.untracked, context={"request": request}).data
		self.assertFalse(data["tracked"])
		self.assertEqual([s["id"] for s in data["subjects"]], [self.s_ms.pk])


class SubjectLinkQueryCountTests(SubjectLinkFixture):
	def count_queries(self, url):
		self.client.get(url)
		cache.clear()
		with CaptureQueriesContext(connection) as queries:
			response = self.client.get(url)
		self.assertEqual(response.status_code, 200)
		return len(queries), response

	def test_list_query_count_is_constant_with_subject_links(self):
		small, response = self.count_queries("/medicines/?page_size=100")
		rows = len(response.data["results"])
		for i in range(10):
			medicine = self._medicine(f"Bulk subject {i}", f"bulks{i}")
			self._subject_link(medicine, self.s_ms)
			self._subject_link(medicine, self.s_als)
		large, response = self.count_queries("/medicines/?page_size=100")
		self.assertEqual(len(response.data["results"]), rows + 10)
		self.assertEqual(small, large)
		self.assertLessEqual(large, 12)

	def test_list_with_tracked_and_subject_filters_is_constant(self):
		url = f"/medicines/?page_size=100&subject_id={self.s_ms.pk}&tracked=false"
		small, _ = self.count_queries(url)
		for i in range(8):
			self._subject_link(self._medicine(f"Bulk untracked {i}", f"bulku{i}"), self.s_ms)
		large, response = self.count_queries(url)
		self.assertEqual(small, large)
		self.assertGreaterEqual(len(response.data["results"]), 8)

	def test_detail_query_count_is_constant_with_copies(self):
		small, _ = self.count_queries(f"/medicines/{self.lead.pk}/")
		for i in range(6):
			self._medicine(f"Extra copy {i}", "leadium", self.c_ms, grouped_under=self.lead)
		large, response = self.count_queries(f"/medicines/{self.lead.pk}/")
		self.assertEqual(len(response.data["grouped_products"]), 7)
		self.assertEqual(small, large)

	def test_events_query_count_is_constant(self):
		small, _ = self.count_queries("/medicines/events/?page_size=100&tracked=false")
		for i in range(6):
			medicine = self._medicine(f"Bulk event {i}", f"bulke{i}")
			self._subject_link(medicine, self.s_ms)
			self._event(medicine, Kind.AUTHORISED, self.today - timedelta(days=1), Basis.OBSERVED)
		large, response = self.count_queries("/medicines/events/?page_size=100&tracked=false")
		self.assertEqual(len(response.data["results"]), 7)
		self.assertEqual(small, large)


class CategoryCountGroupedCopiesTests(SubjectLinkFixture):
	def counts(self):
		response = self.client.get("/categories/")
		self.assertEqual(response.status_code, 200)
		return {row["category_slug"]: row["medicines_count"] for row in response.data["results"]}

	def test_medicines_count_leaves_out_grouped_copies(self):
		counts = self.counts()
		# C_als: multi, als_only, cross, half (the fixture's four); `copy` is a grouped copy.
		self.assertEqual(counts["riluzole"], 4)

	def test_medicines_count_matches_the_default_list_of_the_category(self):
		counts = self.counts()
		for category in (self.c_ms, self.c_als):
			response = self.client.get(f"/medicines/?category_id={category.pk}")
			self.assertEqual(counts[category.category_slug], response.data["count"])

	def test_a_copy_that_stops_being_a_copy_is_counted(self):
		before = self.counts()["riluzole"]
		Medicine.objects.filter(pk=self.copy.pk).update(grouped_under=None)
		self.assertEqual(self.counts()["riluzole"], before + 1)
