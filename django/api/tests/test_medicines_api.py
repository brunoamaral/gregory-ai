"""
/medicines/ and /medicines/events/ (api/views.py MedicineViewSet, MedicineEventViewSet)
and ``medicines_count`` on /categories/.

A medicine has no subject of its own: it is visible when it has a live link to a
category that has a subject in the caller's medicine scope, which only counts sites
with ``CustomSetting.medicines_enabled`` on. Fixture:

  - site ON:  public,  medicines ON,  scope {S_ms, S_als}
  - site OFF: private, medicines OFF, scope {S_ms, S_other}
  - one organisation owns both

  categories: C_ms {S_ms}, C_als {S_als}, C_other {S_other}, C_both {S_ms, S_als}
  (C_other is outside site ON's scope)

Nothing here calls EMA: medicines and events are built directly.
"""

from datetime import date, timedelta

from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now
from organizations.models import Organization
from rest_framework.test import APIClient

from api.models import APIAccessScheme
from api.tests.visibility_helpers import private_site_publishing, publish_subjects
from gregory.models import (
	CategoryAssignmentSource,
	Medicine,
	MedicineCategoryAssignment,
	MedicineEvent,
	OrganizationApiSettings,
	Subject,
	Team,
	TeamCategory,
)
from mcpauth.tests.helpers import make_user
from sitesettings.models import CustomSetting

Kind = MedicineEvent.Kind
Basis = MedicineEvent.Basis

SOURCE = "European Medicines Agency"


class UncachedClient(APIClient):
	"""APIClient that empties the cache before every request.

	The list endpoints cache their COUNT(*) (api.pagination.CachedCountMixin), keyed on
	the URL and the caller's scope. A test that changes the data and asks again for the
	same URL would otherwise be answered with the old count, and an empty page.
	"""

	def request(self, **kwargs):
		cache.clear()
		return super().request(**kwargs)


def _ids(response, key="id"):
	return [row[key] for row in response.data["results"]]


class MedicineFixture(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.org = Organization.objects.create(name="Medicine Org", slug="medicine-org")
		# The team-addressed /teams/<id>/subjects/<id>/categories/ route is also
		# gated on the organisation being public.
		OrganizationApiSettings.objects.filter(organization=cls.org).update(make_api_public=True)
		cls.team = Team.objects.create(
			organization=cls.org, name="Medicine team", slug="medicine-team"
		)
		cls.s_ms = Subject.objects.create(team=cls.team, subject_name="MS", subject_slug="ms")
		cls.s_als = Subject.objects.create(team=cls.team, subject_name="ALS", subject_slug="als")
		cls.s_other = Subject.objects.create(
			team=cls.team, subject_name="Other", subject_slug="other"
		)
		cls.site_on = publish_subjects(
			cls.s_ms, cls.s_als, organization=cls.org, domain="med-on.example.com", medicines_enabled=True
		)
		cls.site_off = private_site_publishing(
			cls.s_ms, cls.s_other, organization=cls.org, domain="med-off.example.com", medicines_enabled=False
		)

		cls.c_ms = cls._category("Ocrelizumab", "ocrelizumab", cls.s_ms)
		cls.c_als = cls._category("Riluzole", "riluzole", cls.s_als)
		cls.c_other = cls._category("Other thing", "other-thing", cls.s_other)
		cls.c_both = cls._category("Broad", "broad", cls.s_ms, cls.s_als)

		cls.counter = 0
		cls.ocrevus = cls._medicine(
			"Ocrevus",
			"ocrelizumab",
			cls.c_ms,
			authorisation_date=date(2018, 1, 8),
			latest_ec_decision_date=date(2024, 6, 1),
			ema_last_updated=date(2026, 10, 1),
			advanced_therapy=False,
			orphan_medicine=False,
		)
		cls.kesimpta = cls._medicine(
			"Kesimpta",
			"ofatumumab",
			cls.c_ms,
			authorisation_date=date(2021, 3, 26),
			ema_last_updated=date(2026, 9, 1),
			orphan_medicine=True,
		)
		cls.generic = cls._medicine(
			"Dimethyl fumarate Mylan", "dimethyl fumarate", cls.c_ms, generic=True,
			ema_last_updated=date(2026, 8, 1),
		)
		cls.biosimilar = cls._medicine(
			"Tyruko", "natalizumab", cls.c_ms, biosimilar=True, ema_last_updated=date(2026, 7, 1),
		)
		# Linked to two categories: must be listed once however the filters join.
		cls.multi = cls._medicine(
			"Riluzole Zentiva", "riluzole", cls.c_ms, cls.c_als, ema_last_updated=date(2026, 6, 1),
			status="Withdrawn",
		)
		cls.als_only = cls._medicine(
			"Exservan", "riluzole", cls.c_als, ema_last_updated=date(2026, 5, 1), is_combination=True,
		)
		# The out-of-scope link is the only one: invisible on site ON.
		cls.hidden = cls._medicine("Hidden", "hiddenium", cls.c_other, ema_last_updated=date(2026, 4, 1))
		# The only link is suppressed: invisible everywhere.
		cls.suppressed = cls._medicine("Suppressed", "suppressium", ema_last_updated=date(2026, 3, 1))
		cls._link(cls.suppressed, cls.c_ms, suppressed=True)
		# Live link to C_ms (subject S_ms) and live link to C_als (subject S_als).
		cls.cross = cls._medicine("Cross", "crossium", cls.c_ms, cls.c_als, ema_last_updated=date(2026, 2, 1))
		# Live link to C_als, suppressed link to C_ms.
		cls.half = cls._medicine("Half", "halfium", cls.c_als, ema_last_updated=date(2026, 1, 1))
		cls._link(cls.half, cls.c_ms, suppressed=True)
		cls.undated = cls._medicine("Undated", "undatium", cls.c_ms, ema_last_updated=None)

		cls.today = date.today()
		cls.ev_auth = cls._event(cls.ocrevus, Kind.AUTHORISED, date(2018, 1, 8), Basis.RECONSTRUCTED)
		cls.ev_opinion = cls._event(cls.ocrevus, Kind.OPINION_POSITIVE, date(2017, 11, 1), Basis.RECONSTRUCTED)
		cls.ev_new = cls._event(
			cls.kesimpta, Kind.EC_DECISION, cls.today - timedelta(days=2), Basis.OBSERVED
		)
		cls.ev_generic = cls._event(cls.generic, Kind.AUTHORISED, cls.today, Basis.OBSERVED)
		cls.ev_multi = cls._event(cls.multi, Kind.APPLICATION_WITHDRAWN, cls.today - timedelta(days=1), Basis.OBSERVED)
		cls.ev_hidden = cls._event(cls.hidden, Kind.AUTHORISED, cls.today, Basis.OBSERVED)
		cls.ev_suppressed = cls._event(cls.suppressed, Kind.AUTHORISED, cls.today, Basis.OBSERVED)
		cls.ev_ended = cls._event(cls.als_only, Kind.ENDED, cls.today - timedelta(days=3), Basis.OBSERVED, to_value="Withdrawn")
		cls.ev_cross = cls._event(cls.cross, Kind.AUTHORISED, cls.today - timedelta(days=4), Basis.OBSERVED)

	@classmethod
	def _category(cls, name, slug, *subjects):
		category = TeamCategory.objects.create(team=cls.team, category_name=name, category_slug=slug)
		category.subjects.add(*subjects)
		return category

	@classmethod
	def _medicine(cls, name, inn, *categories, **fields):
		cls.counter += 1
		values = {
			"ema_product_number": f"EMEA/H/C/{800000 + cls.counter}",
			"name": name,
			"inn": inn,
			"substances": [inn],
			"status": "Authorised",
			"indication": f"Indication of {name}.",
			"url": f"https://www.ema.europa.eu/en/medicines/human/EPAR/{name.lower().replace(' ', '-')}",
			"raw": {"name_of_medicine": name},
		}
		values.update(fields)
		medicine = Medicine.objects.create(**values)
		for category in categories:
			cls._link(medicine, category)
		return medicine

	@classmethod
	def _link(cls, medicine, category, suppressed=False):
		return MedicineCategoryAssignment.objects.create(
			medicine=medicine,
			teamcategory=category,
			suppressed=suppressed,
			source=CategoryAssignmentSource.AUTOMATIC,
		)

	@classmethod
	def _event(cls, medicine, kind, event_date, basis, **extra):
		return MedicineEvent.objects.create(
			medicine=medicine, kind=kind, event_date=event_date, basis=basis, **extra
		)

	def setUp(self):
		cache.clear()
		self.client = UncachedClient()

	def key_for(self, site):
		return APIAccessScheme.objects.create(
			client_name=f"key-{site.pk}",
			client_contacts="k@example.com",
			organization=self.org,
			site=site,
			ip_addresses="",
			begin_date=now() - timedelta(days=1),
			end_date=now() + timedelta(days=30),
		)

	def names(self, response):
		return sorted(r["name"] for r in response.data["results"])


# The products site ON shows with the generics default: everything with a live link to
# C_ms, C_als or C_both, minus generics and biosimilars.
VISIBLE_BY_DEFAULT = [
	"Cross", "Exservan", "Half", "Kesimpta", "Ocrevus", "Riluzole Zentiva", "Undated",
]


class MedicineVisibilityTests(MedicineFixture):
	def test_anonymous_list_on_a_site_with_medicines(self):
		response = self.client.get("/medicines/")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(self.names(response), VISIBLE_BY_DEFAULT)

	def test_a_medicine_linked_only_to_an_out_of_scope_category_is_invisible(self):
		self.assertNotIn("Hidden", self.names(self.client.get("/medicines/?include_generics=true")))
		self.assertEqual(self.client.get(f"/medicines/{self.hidden.pk}/").status_code, 404)

	def test_a_medicine_whose_only_link_is_suppressed_is_invisible(self):
		self.assertNotIn("Suppressed", self.names(self.client.get("/medicines/?include_generics=true")))
		self.assertEqual(self.client.get(f"/medicines/{self.suppressed.pk}/").status_code, 404)

	def test_a_site_with_the_switch_off_sees_an_empty_list_and_404s(self):
		key = self.key_for(self.site_off)
		response = self.client.get("/medicines/", HTTP_AUTHORIZATION=key.api_key)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data["results"], [])
		self.assertEqual(response.data["count"], 0)
		detail = self.client.get(f"/medicines/{self.ocrevus.pk}/", HTTP_AUTHORIZATION=key.api_key)
		self.assertEqual(detail.status_code, 404)
		events = self.client.get("/medicines/events/", HTTP_AUTHORIZATION=key.api_key)
		self.assertEqual(events.data["results"], [])

	def test_site_bound_key_on_a_site_with_medicines(self):
		key = self.key_for(self.site_on)
		response = self.client.get("/medicines/", HTTP_AUTHORIZATION=key.api_key)
		self.assertEqual(self.names(response), VISIBLE_BY_DEFAULT)

	def test_the_count_is_not_shared_with_a_caller_whose_medicine_scope_differs(self):
		# Same subject scope as site ON, medicines off: the two callers differ only in
		# their medicine scope, so a cached count keyed on subjects alone would be shared.
		same_subjects = private_site_publishing(
			self.s_ms, self.s_als, organization=self.org, domain="med-same.example.com",
			medicines_enabled=False,
		)
		key = self.key_for(same_subjects)
		cached = APIClient()
		anonymous = cached.get("/medicines/")
		self.assertEqual(anonymous.data["count"], len(VISIBLE_BY_DEFAULT))
		other = cached.get("/medicines/", HTTP_AUTHORIZATION=key.api_key)
		self.assertEqual(other.data["count"], 0)
		self.assertEqual(other.data["results"], [])
		# And back: the empty scope did not poison the first caller's entry.
		self.assertEqual(cached.get("/medicines/").data["count"], len(VISIBLE_BY_DEFAULT))

	def test_detail_shows_only_visible_live_categories(self):
		# `multi` is in C_ms and C_als; add a link into a category outside the scope.
		self._link(self.multi, self.c_other)
		response = self.client.get(f"/medicines/{self.multi.pk}/")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(
			[c["category_slug"] for c in response.data["categories"]], ["ocrelizumab", "riluzole"]
		)

	def test_half_suppressed_medicine_lists_only_its_live_category(self):
		response = self.client.get(f"/medicines/{self.half.pk}/")
		self.assertEqual([c["category_slug"] for c in response.data["categories"]], ["riluzole"])

	def test_ambiguous_anonymous_site_fails_closed_like_other_endpoints(self):
		publish_subjects(self.s_ms, organization=self.org, domain="med-second.example.com")
		self.assertEqual(self.client.get("/medicines/").status_code, 400)
		self.assertEqual(self.client.get("/medicines/events/").status_code, 400)
		self.assertEqual(self.client.get(f"/medicines/?site_id={self.site_on.pk}").status_code, 200)

	def test_authenticated_member_sees_the_sites_of_their_organisation_with_medicines(self):
		# The organisation owns site ON (switch on) and site OFF (switch off): only
		# the first contributes.
		self.client.force_login(make_user("med-member", self.org))
		self.assertEqual(self.names(self.client.get("/medicines/")), VISIBLE_BY_DEFAULT)

	def test_authenticated_user_outside_the_organisation_sees_none(self):
		self.client.force_login(make_user("med-outsider"))
		self.assertEqual(self.client.get("/medicines/?site_id=%d" % self.site_on.pk).data["results"], [])

	def test_include_public_adds_only_public_sites_with_medicines(self):
		key = self.key_for(self.site_off)
		plain = self.client.get("/medicines/", HTTP_AUTHORIZATION=key.api_key)
		self.assertEqual(plain.data["results"], [])
		wider = self.client.get("/medicines/?include_public=true", HTTP_AUTHORIZATION=key.api_key)
		self.assertEqual(self.names(wider), VISIBLE_BY_DEFAULT)

	def test_csv_and_all_results_are_not_offered(self):
		self.assertEqual(self.client.get("/medicines/?format=csv").status_code, 404)
		self.assertEqual(self.client.get("/medicines/events/?format=csv").status_code, 404)
		response = self.client.get("/medicines/?all_results=true&page_size=2")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(len(response.data["results"]), 2)
		self.assertEqual(self.client.get("/medicines/?page_size=100000").data["page_size"], 100)

	def test_write_methods_are_refused(self):
		# Anonymous: not allowed to write at all. Signed in: the routes only read.
		for method, url in (
			("post", "/medicines/"),
			("delete", f"/medicines/{self.ocrevus.pk}/"),
			("post", "/medicines/events/"),
		):
			with self.subTest(method=method, url=url):
				self.assertIn(getattr(self.client, method)(url).status_code, (401, 403))
		self.client.force_login(make_user("med-writer", self.org))
		for method, url in (
			("post", "/medicines/"),
			("patch", f"/medicines/{self.ocrevus.pk}/"),
			("delete", f"/medicines/{self.ocrevus.pk}/"),
			("post", "/medicines/events/"),
		):
			with self.subTest(method=method, url=url, signed_in=True):
				self.assertEqual(getattr(self.client, method)(url).status_code, 405)


class MedicineSerializerTests(MedicineFixture):
	def test_row_fields_and_attribution(self):
		response = self.client.get(f"/medicines/?substance=ocrelizumab")
		row = response.data["results"][0]
		self.assertEqual(row["source"], SOURCE)
		self.assertEqual(row["name"], "Ocrevus")
		self.assertEqual(row["ema_product_number"], self.ocrevus.ema_product_number)
		self.assertEqual(row["substances"], ["ocrelizumab"])
		self.assertEqual(row["authorisation_date"], "2018-01-08")
		self.assertEqual(row["latest_ec_decision_date"], "2024-06-01")
		self.assertEqual(
			row["categories"],
			[{"id": self.c_ms.pk, "category_name": "Ocrelizumab", "category_slug": "ocrelizumab"}],
		)
		self.assertEqual(row["url"], self.ocrevus.url)
		for hidden in ("raw", "in_latest_export", "first_seen_at", "last_synced_at"):
			self.assertNotIn(hidden, row)
		self.assertNotIn("events", row)

	def test_detail_has_events_newest_first(self):
		response = self.client.get(f"/medicines/{self.ocrevus.pk}/")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data["source"], SOURCE)
		events = response.data["events"]
		self.assertEqual([e["kind"] for e in events], ["authorised", "opinion_positive"])
		self.assertEqual(events[0]["kind_label"], "Authorised in the EU")
		self.assertEqual(events[0]["event_date"], "2018-01-08")
		self.assertNotIn("medicine", events[0])

	def test_a_single_record_is_returned_even_when_it_is_a_generic(self):
		response = self.client.get(f"/medicines/{self.generic.pk}/")
		self.assertEqual(response.status_code, 200)
		self.assertTrue(response.data["generic"])

	def test_non_numeric_id_is_404(self):
		self.assertEqual(self.client.get("/medicines/abc/").status_code, 404)


class MedicineGenericsTests(MedicineFixture):
	def test_generics_and_biosimilars_are_hidden_by_default(self):
		names = self.names(self.client.get("/medicines/"))
		self.assertNotIn("Dimethyl fumarate Mylan", names)
		self.assertNotIn("Tyruko", names)

	def test_include_generics_lists_them(self):
		names = self.names(self.client.get("/medicines/?include_generics=true"))
		self.assertIn("Dimethyl fumarate Mylan", names)
		self.assertIn("Tyruko", names)

	def test_include_generics_false_is_the_default(self):
		self.assertEqual(
			self.names(self.client.get("/medicines/?include_generics=false")), VISIBLE_BY_DEFAULT
		)

	def test_events_hide_generic_events_by_default(self):
		ids = _ids(self.client.get("/medicines/events/"))
		self.assertNotIn(self.ev_generic.pk, ids)
		self.assertIn(self.ev_generic.pk, _ids(self.client.get("/medicines/events/?include_generics=true")))


class MedicineFilterTests(MedicineFixture):
	def get(self, query):
		response = self.client.get(f"/medicines/?{query}")
		self.assertEqual(response.status_code, 200, response.content)
		return self.names(response)

	def test_substance_matches_an_element_ignoring_case(self):
		self.assertEqual(self.get("substance=OCRELIZUMAB"), ["Ocrevus"])
		self.assertEqual(self.get("substance=riluzole"), ["Exservan", "Riluzole Zentiva"])
		self.assertEqual(self.get("substance=ocrelizu"), [])

	def test_substance_matches_any_element_of_a_combination(self):
		Medicine.objects.filter(pk=self.kesimpta.pk).update(substances=["ofatumumab", "second one"])
		self.assertEqual(self.get("substance=second%20%20one"), ["Kesimpta"])

	def test_name_matches_a_substring_ignoring_case(self):
		self.assertEqual(self.get("name=OCREVUS"), ["Ocrevus"])
		self.assertEqual(self.get("name=cre"), ["Ocrevus"])
		self.assertEqual(self.get("name=riluzole"), ["Riluzole Zentiva"])
		self.assertEqual(self.get("name=nothing-like-this"), [])

	def test_name_finds_a_product_by_its_former_name(self):
		Medicine.objects.filter(pk=self.kesimpta.pk).update(
			name="Corbilta (previously Levodopa/Carbidopa/Entacapone Sandoz)"
		)
		self.assertEqual(
			self.get("name=corbilta"), ["Corbilta (previously Levodopa/Carbidopa/Entacapone Sandoz)"]
		)
		self.assertEqual(len(self.get("name=entacapone")), 1)

	def test_name_combines_with_the_other_filters(self):
		self.assertEqual(self.get(f"name=ocrevus&category_id={self.c_ms.pk}"), ["Ocrevus"])
		self.assertEqual(self.get(f"name=ocrevus&category_id={self.c_als.pk}"), [])
		self.assertEqual(self.get("name=ocrevus&status=Withdrawn"), [])
		self.assertEqual(self.get("name=riluzole&status=Withdrawn"), ["Riluzole Zentiva"])
		self.assertEqual(self.get("name=ocrevus&substance=ocrelizumab"), ["Ocrevus"])
		self.assertEqual(self.get("name=ocrevus&substance=riluzole"), [])

	def test_name_never_matches_a_medicine_the_caller_cannot_see(self):
		# Linked only to a category outside the site's scope, or only by a suppressed link.
		self.assertEqual(self.get("name=hidden"), [])
		self.assertEqual(self.get("name=suppressed"), [])

	def test_name_leaves_generics_to_include_generics(self):
		self.assertEqual(self.get("name=tyruko"), [])
		self.assertEqual(self.get("name=tyruko&include_generics=true"), ["Tyruko"])

	def test_name_does_not_repeat_a_medicine_in_two_categories(self):
		self.assertEqual(self.get("name=riluzole%20zentiva"), ["Riluzole Zentiva"])

	def test_status_is_case_insensitive(self):
		self.assertEqual(self.get("status=withdrawn"), ["Riluzole Zentiva"])

	def test_opinion_status(self):
		Medicine.objects.filter(pk=self.kesimpta.pk).update(opinion_status="Positive")
		self.assertEqual(self.get("opinion_status=positive"), ["Kesimpta"])

	def test_boolean_filters(self):
		self.assertEqual(self.get("orphan_medicine=true"), ["Kesimpta"])
		self.assertNotIn("Kesimpta", self.get("orphan_medicine=false"))
		self.assertEqual(self.get("is_combination=true"), ["Exservan"])
		Medicine.objects.filter(pk=self.ocrevus.pk).update(advanced_therapy=True)
		self.assertEqual(self.get("advanced_therapy=true"), ["Ocrevus"])

	def test_authorised_date_range_is_inclusive_and_on_the_first_approval(self):
		self.assertEqual(self.get("authorised_after=2021-03-26"), ["Kesimpta"])
		self.assertEqual(self.get("authorised_before=2018-01-08"), ["Ocrevus"])
		self.assertEqual(self.get("authorised_after=2018-01-08&authorised_before=2021-03-26"), ["Kesimpta", "Ocrevus"])

	def test_invalid_date_is_a_400(self):
		self.assertEqual(self.client.get("/medicines/?authorised_after=soon").status_code, 400)

	def test_filters_combine(self):
		self.assertEqual(self.get("substance=riluzole&status=Authorised"), ["Exservan"])


class MedicineOrderingTests(MedicineFixture):
	def order(self, ordering):
		response = self.client.get(f"/medicines/?ordering={ordering}")
		self.assertEqual(response.status_code, 200)
		return [r["name"] for r in response.data["results"]]

	def test_default_is_most_recently_updated_first_with_undated_last(self):
		names = [r["name"] for r in self.client.get("/medicines/").data["results"]]
		self.assertEqual(names[:2], ["Ocrevus", "Kesimpta"])
		self.assertEqual(names[-1], "Undated")

	def test_authorisation_date_both_directions_keep_missing_dates_last(self):
		up = self.order("authorisation_date")
		self.assertEqual(up[:2], ["Ocrevus", "Kesimpta"])
		down = self.order("-authorisation_date")
		self.assertEqual(down[:2], ["Kesimpta", "Ocrevus"])
		self.assertEqual(set(up[2:]), set(down[2:]))

	def test_latest_ec_decision_date_is_an_ordering(self):
		self.assertEqual(self.order("-latest_ec_decision_date")[0], "Ocrevus")

	def test_unknown_ordering_is_ignored(self):
		self.assertEqual(self.client.get("/medicines/?ordering=raw").status_code, 200)


class MedicineLinkFilterTests(MedicineFixture):
	"""category_id, category_slug, subject_id and site_id."""

	def get(self, path, query):
		response = self.client.get(f"{path}?{query}")
		self.assertEqual(response.status_code, 200, response.content)
		return response

	def meds(self, query):
		return self.names(self.get("/medicines/", query))

	def event_ids(self, query):
		return sorted(_ids(self.get("/medicines/events/", query)))

	def test_a_single_category_id_is_the_plain_filter(self):
		self.assertEqual(
			self.meds(f"category_id={self.c_als.pk}"),
			["Cross", "Exservan", "Half", "Riluzole Zentiva"],
		)

	def test_a_comma_separated_list_matches_any(self):
		self.assertEqual(
			self.meds(f"category_id={self.c_ms.pk},{self.c_als.pk}"),
			["Cross", "Exservan", "Half", "Kesimpta", "Ocrevus", "Riluzole Zentiva", "Undated"],
		)

	def test_a_list_never_returns_a_product_twice(self):
		response = self.get("/medicines/", f"category_id={self.c_ms.pk},{self.c_als.pk}&include_generics=true")
		ids = _ids(response)
		self.assertEqual(len(ids), len(set(ids)))
		self.assertEqual(response.data["count"], len(ids))
		self.assertEqual(ids.count(self.multi.pk), 1)
		# Same on the events of a product in both categories.
		event_ids = _ids(self.get("/medicines/events/", f"category_id={self.c_ms.pk},{self.c_als.pk}"))
		self.assertEqual(len(event_ids), len(set(event_ids)))

	def test_list_with_duplicates_and_spaces_is_fine(self):
		self.assertEqual(
			self.meds(f"category_id={self.c_ms.pk},{self.c_ms.pk}"),
			self.meds(f"category_id={self.c_ms.pk}"),
		)

	def test_an_invalid_id_list_is_a_400(self):
		for path in ("/medicines/", "/medicines/events/"):
			for bad in ("6,abc", "abc", "1.5", "0", "99999999999999999999"):
				with self.subTest(path=path, value=bad):
					self.assertEqual(self.client.get(f"{path}?category_id={bad}").status_code, 400)
		for param in ("subject_id", "site_id"):
			self.assertEqual(self.client.get(f"/medicines/?{param}=abc").status_code, 400)

	def test_category_slug(self):
		self.assertEqual(self.meds("category_slug=ocrelizumab"), ["Cross", "Kesimpta", "Ocrevus", "Riluzole Zentiva", "Undated"])
		self.assertEqual(self.meds("category_slug=no-such-category"), [])

	def test_subject_id(self):
		self.assertEqual(
			self.meds(f"subject_id={self.s_als.pk}"),
			["Cross", "Exservan", "Half", "Riluzole Zentiva"],
		)

	def test_subject_id_reaches_through_a_category_with_several_subjects(self):
		self._link(self.ocrevus, self.c_both)
		self.assertIn("Ocrevus", self.meds(f"subject_id={self.s_als.pk}"))

	def test_a_suppressed_link_never_satisfies_any_link_filter(self):
		# `half` has a suppressed link to C_ms and a live one to C_als; `suppressed` has
		# only a suppressed link to C_ms.
		for query in (
			f"category_id={self.c_ms.pk}",
			"category_slug=ocrelizumab",
			f"subject_id={self.s_ms.pk}",
			f"site_id={self.site_on.pk}",
		):
			with self.subTest(query=query):
				names = self.meds(f"{query}&include_generics=true")
				self.assertNotIn("Suppressed", names)
		self.assertNotIn("Half", self.meds(f"category_id={self.c_ms.pk}"))
		self.assertNotIn("Half", self.meds("category_slug=ocrelizumab"))
		self.assertNotIn("Half", self.meds(f"subject_id={self.s_ms.pk}"))
		self.assertIn("Half", self.meds(f"subject_id={self.s_als.pk}"))
		self.assertNotIn(self.ev_suppressed.pk, self.event_ids(f"category_id={self.c_ms.pk}"))

	def test_subject_and_category_must_hold_on_the_same_link(self):
		# `cross` is linked to C_ms (S_ms) and C_als (S_als): not to a category that is
		# both "category C_als" and "subject S_ms".
		names = self.meds(f"subject_id={self.s_ms.pk}&category_id={self.c_als.pk}")
		self.assertNotIn("Cross", names)
		self.assertNotIn("Riluzole Zentiva", names)
		self.assertEqual(names, [])
		# The consistent pairs do match.
		self.assertIn("Cross", self.meds(f"subject_id={self.s_als.pk}&category_id={self.c_als.pk}"))
		self.assertIn("Cross", self.meds(f"subject_id={self.s_ms.pk}&category_id={self.c_ms.pk}"))

	def test_subject_and_category_with_a_two_subject_category_match_the_one_link(self):
		self._link(self.ocrevus, self.c_both)
		self.assertEqual(
			self.meds(f"subject_id={self.s_als.pk}&category_id={self.c_both.pk}"), ["Ocrevus"]
		)

	def test_category_slug_and_category_id_must_hold_on_the_same_link(self):
		self.assertEqual(self.meds(f"category_id={self.c_als.pk}&category_slug=ocrelizumab"), [])
		self.assertIn("Cross", self.meds(f"category_id={self.c_ms.pk}&category_slug=ocrelizumab"))

	def test_a_link_the_caller_cannot_see_does_not_satisfy_a_filter(self):
		# `hidden` is linked only to C_other (S_other), outside site ON's scope; the
		# category filter must not reveal it, and must not leak the link's existence.
		self.assertEqual(self.meds(f"category_id={self.c_other.pk}"), [])
		self.assertEqual(self.meds(f"subject_id={self.s_other.pk}"), [])
		self.assertEqual(self.event_ids(f"category_id={self.c_other.pk}"), [])
		# A medicine that also has a visible link is visible, but the hidden category
		# is still not a way in.
		self._link(self.ocrevus, self.c_other)
		self.assertEqual(self.meds(f"category_id={self.c_other.pk}"), [])
		self.assertEqual(self.meds(f"subject_id={self.s_other.pk}"), [])

	def test_site_id_keeps_medicines_in_that_sites_scope(self):
		self.assertEqual(self.meds(f"site_id={self.site_on.pk}"), VISIBLE_BY_DEFAULT)

	def test_site_id_with_medicines_off_returns_nothing(self):
		self.assertEqual(self.meds(f"site_id={self.site_off.pk}"), [])
		self.assertEqual(self.event_ids(f"site_id={self.site_off.pk}"), [])
		# Even though the off site's scope names S_ms, which has medicines on site ON.
		self.assertEqual(self.meds(f"site_id={self.site_off.pk}&subject_id={self.s_ms.pk}"), [])

	def test_site_id_of_an_unknown_site_returns_nothing(self):
		self.assertEqual(self.meds("site_id=999999"), [])

	def test_site_id_combines_with_category_id(self):
		self.assertEqual(
			self.meds(f"site_id={self.site_on.pk}&category_id={self.c_als.pk}"),
			["Cross", "Exservan", "Half", "Riluzole Zentiva"],
		)
		self.assertEqual(self.meds(f"site_id={self.site_on.pk}&category_id={self.c_other.pk}"), [])

	def test_a_category_outside_the_sites_scope_does_not_match_site_id(self):
		self._link(self.kesimpta, self.c_other)
		self.assertEqual(self.meds(f"site_id={self.site_on.pk}&category_id={self.c_other.pk}"), [])

	def test_events_link_filters_follow_the_events_medicine(self):
		self.assertEqual(
			self.event_ids(f"category_id={self.c_ms.pk}"),
			sorted([self.ev_auth.pk, self.ev_opinion.pk, self.ev_new.pk, self.ev_multi.pk, self.ev_cross.pk]),
		)
		self.assertEqual(
			self.event_ids(f"category_id={self.c_als.pk}"),
			sorted([self.ev_multi.pk, self.ev_ended.pk, self.ev_cross.pk]),
		)
		self.assertEqual(self.event_ids(f"subject_id={self.s_ms.pk}&category_id={self.c_als.pk}"), [])
		self.assertEqual(self.event_ids(f"category_slug=riluzole"), self.event_ids(f"category_id={self.c_als.pk}"))


class MedicineEventTests(MedicineFixture):
	def test_list_is_newest_first_and_visibility_applies_through_the_medicine(self):
		response = self.client.get("/medicines/events/")
		self.assertEqual(response.status_code, 200)
		ids = _ids(response)
		dates = [r["event_date"] for r in response.data["results"]]
		self.assertEqual(dates, sorted(dates, reverse=True))
		self.assertNotIn(self.ev_hidden.pk, ids)
		self.assertNotIn(self.ev_suppressed.pk, ids)
		self.assertNotIn(self.ev_generic.pk, ids)
		self.assertEqual(
			sorted(ids),
			sorted(
				[
					self.ev_auth.pk,
					self.ev_opinion.pk,
					self.ev_new.pk,
					self.ev_multi.pk,
					self.ev_ended.pk,
					self.ev_cross.pk,
				]
			),
		)

	def test_ties_break_on_newest_id(self):
		a = self._event(self.ocrevus, Kind.STATUS_CHANGED, self.today, Basis.OBSERVED, from_value="a", to_value="b")
		b = self._event(self.kesimpta, Kind.STATUS_CHANGED, self.today, Basis.OBSERVED, from_value="a", to_value="b")
		ids = _ids(self.client.get("/medicines/events/?kind=status_changed"))
		self.assertEqual(ids, [b.pk, a.pk])

	def test_row_shape(self):
		response = self.client.get(f"/medicines/events/?kind=ended")
		row = response.data["results"][0]
		self.assertEqual(
			set(row),
			{"id", "kind", "kind_label", "event_date", "basis", "from_value", "to_value", "medicine", "source"},
		)
		self.assertEqual(row["kind"], "ended")
		self.assertEqual(row["kind_label"], self.ev_ended.in_words)
		self.assertEqual(row["kind_label"], "Authorisation ended (Withdrawn)")
		self.assertEqual(row["basis"], "observed")
		self.assertEqual(row["to_value"], "Withdrawn")
		self.assertEqual(row["source"], SOURCE)
		self.assertEqual(
			row["medicine"],
			{"id": self.als_only.pk, "name": "Exservan", "inn": "riluzole", "url": self.als_only.url},
		)

	def test_kind_label_is_the_feeds_wording(self):
		response = self.client.get("/medicines/events/?kind=authorised")
		self.assertEqual({r["kind_label"] for r in response.data["results"]}, {"Authorised in the EU"})
		response = self.client.get("/medicines/events/?kind=ec_decision")
		self.assertEqual(response.data["results"][0]["kind_label"], "New European Commission decision")

	def test_kind_filter_single_list_and_invalid(self):
		self.assertEqual(
			sorted(r["kind"] for r in self.client.get("/medicines/events/?kind=authorised,opinion_positive").data["results"]),
			["authorised", "authorised", "opinion_positive"],
		)
		self.assertEqual(self.client.get("/medicines/events/?kind=teleported").status_code, 400)

	def test_basis_filter(self):
		rows = self.client.get("/medicines/events/?basis=reconstructed").data["results"]
		self.assertEqual({r["basis"] for r in rows}, {"reconstructed"})
		self.assertEqual({r["id"] for r in rows}, {self.ev_auth.pk, self.ev_opinion.pk})
		self.assertEqual(self.client.get("/medicines/events/?basis=guessed").status_code, 400)

	def test_event_date_range_is_inclusive(self):
		after = self.today - timedelta(days=2)
		ids = _ids(self.client.get(f"/medicines/events/?event_date_after={after.isoformat()}"))
		self.assertEqual(sorted(ids), sorted([self.ev_new.pk, self.ev_multi.pk]))
		before = date(2018, 1, 8)
		ids = _ids(self.client.get(f"/medicines/events/?event_date_before={before.isoformat()}"))
		self.assertEqual(sorted(ids), sorted([self.ev_auth.pk, self.ev_opinion.pk]))
		self.assertEqual(self.client.get("/medicines/events/?event_date_after=nope").status_code, 400)

	def test_there_is_no_detail_route(self):
		self.assertEqual(self.client.get(f"/medicines/events/{self.ev_auth.pk}/").status_code, 404)


class MedicineRouteOrderTests(MedicineFixture):
	def test_events_is_not_read_as_a_medicine_id(self):
		response = self.client.get("/medicines/events/")
		self.assertEqual(response.status_code, 200)
		self.assertIn("kind", response.data["results"][0])
		self.assertNotIn("ema_product_number", response.data["results"][0])

	def test_routes_resolve_to_the_right_viewsets(self):
		from django.urls import resolve

		self.assertEqual(resolve("/medicines/events/").func.cls.__name__, "MedicineEventViewSet")
		self.assertEqual(resolve("/medicines/").func.cls.__name__, "MedicineViewSet")
		self.assertEqual(resolve("/medicines/12/").func.cls.__name__, "MedicineViewSet")

	def test_api_root_lists_both(self):
		root = self.client.get("/")
		self.assertEqual(root.status_code, 200)
		self.assertIn("medicines", root.data)
		self.assertIn("medicines/events", root.data)


class MedicineQueryCountTests(MedicineFixture):
	"""A list costs a fixed number of queries whatever the number of rows."""

	def count_queries(self, url):
		# One uncounted request first: the first call in a process also fills
		# per-process caches (the Site lookup), which later calls do not pay for.
		self.client.get(url)
		cache.clear()
		with CaptureQueriesContext(connection) as queries:
			response = self.client.get(url)
		self.assertEqual(response.status_code, 200)
		return len(queries), response

	def test_medicines_list_does_not_query_per_row(self):
		small, response = self.count_queries("/medicines/?page_size=100")
		rows = len(response.data["results"])
		for i in range(12):
			medicine = self._medicine(f"Bulk {i}", f"bulk{i}", self.c_ms, self.c_als)
			self._event(medicine, Kind.AUTHORISED, self.today, Basis.OBSERVED)
		large, response = self.count_queries("/medicines/?page_size=100")
		self.assertEqual(len(response.data["results"]), rows + 12)
		self.assertEqual(small, large)
		# The categories are on every row without having cost a query each.
		self.assertTrue(all(len(r["categories"]) >= 1 for r in response.data["results"]))
		self.assertLessEqual(large, 12)

	def test_medicines_list_with_link_filters_does_not_query_per_row(self):
		url = f"/medicines/?page_size=100&subject_id={self.s_ms.pk}&category_id={self.c_ms.pk},{self.c_als.pk}"
		small, _ = self.count_queries(url)
		for i in range(8):
			self._medicine(f"Bulk {i}", f"bulk{i}", self.c_ms)
		large, _ = self.count_queries(url)
		self.assertEqual(small, large)

	def test_events_list_does_not_query_per_row(self):
		small, response = self.count_queries("/medicines/events/?page_size=100")
		rows = len(response.data["results"])
		for i in range(12):
			medicine = self._medicine(f"Bulk {i}", f"bulk{i}", self.c_ms)
			self._event(medicine, Kind.AUTHORISED, self.today, Basis.OBSERVED)
		large, response = self.count_queries("/medicines/events/?page_size=100")
		self.assertEqual(len(response.data["results"]), rows + 12)
		self.assertEqual(small, large)
		self.assertLessEqual(large, 10)

	def test_detail_query_count_is_fixed(self):
		small, _ = self.count_queries(f"/medicines/{self.ocrevus.pk}/")
		for i in range(6):
			self._event(self.ocrevus, Kind.EC_DECISION, date(2019, 1, 1) + timedelta(days=i), Basis.OBSERVED)
		large, response = self.count_queries(f"/medicines/{self.ocrevus.pk}/")
		self.assertEqual(len(response.data["events"]), 8)
		self.assertEqual(small, large)


class CategoryMedicinesCountTests(MedicineFixture):
	def counts(self, response):
		self.assertEqual(response.status_code, 200)
		return {row["category_slug"]: row["medicines_count"] for row in response.data["results"]}

	def test_counts_live_visible_links_without_generics(self):
		counts = self.counts(self.client.get("/categories/"))
		# C_ms: ocrevus, kesimpta, multi, cross, undated (live, not generic);
		# the generic, the biosimilar, `suppressed` and `half` (both suppressed) are out.
		self.assertEqual(counts["ocrelizumab"], 5)
		# C_als: multi, als_only, cross, half.
		self.assertEqual(counts["riluzole"], 4)
		self.assertEqual(counts["broad"], 0)

	def test_matches_the_default_medicines_list_of_the_category(self):
		counts = self.counts(self.client.get("/categories/"))
		for category in (self.c_ms, self.c_als):
			response = self.client.get(f"/medicines/?category_id={category.pk}")
			self.assertEqual(counts[category.category_slug], response.data["count"])

	def test_a_suppressed_link_does_not_count(self):
		before = self.counts(self.client.get("/categories/"))["ocrelizumab"]
		MedicineCategoryAssignment.objects.filter(
			medicine=self.ocrevus, teamcategory=self.c_ms
		).update(suppressed=True)
		self.assertEqual(self.counts(self.client.get("/categories/"))["ocrelizumab"], before - 1)

	def test_a_site_with_the_switch_off_counts_zero(self):
		key = self.key_for(self.site_off)
		counts = self.counts(self.client.get("/categories/", HTTP_AUTHORIZATION=key.api_key))
		# C_ms is in site OFF's scope (S_ms), and has live medicines, but the site
		# does not show medicines.
		self.assertEqual(counts["ocrelizumab"], 0)
		self.assertTrue(all(count == 0 for count in counts.values()))

	def test_category_outside_the_medicine_scope_counts_zero(self):
		# Visible to an identified member through site OFF, but its subject is not in
		# any medicines-enabled scope.
		CustomSetting.objects.filter(site=self.site_on).update(medicines_enabled=False)
		cache.clear()
		counts = self.counts(self.client.get(f"/categories/?site_id={self.site_on.pk}"))
		self.assertTrue(all(count == 0 for count in counts.values()))

	def test_the_by_team_and_subject_endpoint_carries_the_count(self):
		response = self.client.get(
			f"/teams/{self.team.pk}/subjects/{self.s_ms.pk}/categories/"
		)
		self.assertEqual(response.status_code, 200, response.content)
		counts = self.counts(response)
		self.assertEqual(counts["ocrelizumab"], 5)
