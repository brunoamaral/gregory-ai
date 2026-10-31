"""
/medicines/drugs/ (api/views.py MedicineDrugViewSet): the medicines list paged by drug.

Builds on the substance_key fixture: Gilenya at EMA and at the FDA (plus Tascenso, another
FDA application of fingolimod), Ocrevus at both (and Ocrevus Zunovo), Rebif and Avonex,
Exforge (a combination). Every record gets the key the syncs would give it.
"""

from datetime import date

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from api.tests.test_medicine_substance_key_api import SubstanceKeyFixture
from gregory.models import Medicine, MedicineSubjectLink, Regulator
from gregory.utils.substance_key import substance_key

DRUGS = "/medicines/drugs/"


class DrugFixture(SubstanceKeyFixture):
	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		dates = {
			cls.gilenya_ema: date(2011, 3, 17),
			cls.tascenso: date(2021, 12, 30),
			cls.exforge: date(2007, 1, 17),
			cls.rebif: date(1998, 5, 4),
			cls.avonex_fda: date(1996, 5, 17),
			cls.zunovo: date(2024, 9, 13),
		}
		for medicine, authorised in dates.items():
			Medicine.objects.filter(pk=medicine.pk).update(authorisation_date=authorised)
		# The base fixtures never ran through a sync: key the rest as the syncs would.
		for medicine in Medicine.objects.filter(substance_key=""):
			Medicine.objects.filter(pk=medicine.pk).update(
				substance_key=substance_key(medicine.substances, pk=medicine.pk)
			)

		# FDA only, brand in capitals: shown as "Zeposia".
		cls.zeposia = cls._keyed(
			Regulator.FDA, "ZEPOSIA", ["ozanimod hydrochloride"], cls.c_ms,
			authorisation_date=date(2020, 3, 25), brand_names=["ZEPOSIA"],
		)
		# A duplicate EMA product of Ocrevus: only with include_generics.
		cls.ocrevus_copy = cls._keyed(
			Regulator.EMA, "Ocrevus copy", ["ocrelizumab"], cls.c_ms,
			authorisation_date=date(2018, 1, 9), grouped_under=cls.ocrevus,
		)
		# Same drug, a record only an out-of-scope category links: never shown on site ON.
		cls.ocrevus_hidden = cls._keyed(
			Regulator.EMA, "Ocrevus hidden twin", ["ocrelizumab"], cls.c_other,
			authorisation_date=date(2019, 1, 1),
		)
		# Untracked: visible only through a subject link (EMA approved it for MS).
		cls.kesimpta_untracked = cls._keyed(
			Regulator.EMA, "Kesimpta untracked", ["ofatumumab"],
			authorisation_date=date(2022, 2, 2),
		)
		MedicineSubjectLink.objects.create(
			medicine=cls.kesimpta_untracked, subject=cls.s_ms, matched_on="Multiple sclerosis"
		)
		# No substances at all: its key stands alone.
		cls.keyless = cls._medicine(
			"Keyless", "", cls.c_ms, substances=[], authorisation_date=date(2005, 5, 5)
		)
		Medicine.objects.filter(pk=cls.keyless.pk).update(substance_key=f"medicine-{cls.keyless.pk}")
		# A key with a dot, to see how the detail route treats it.
		cls.dotted = cls._keyed(
			Regulator.EMA, "Dotted", ["compound 1.5"], cls.c_ms, authorisation_date=date(2003, 3, 3)
		)
		# Undated: sorts last in both directions.
		cls.undated_drug = cls._keyed(Regulator.EMA, "Zzz undated", ["zzzumab"], cls.c_ms)

	def drugs(self, query="", **kwargs):
		response = self.client.get(f"{DRUGS}?{query}", **kwargs)
		self.assertEqual(response.status_code, 200, response.content)
		return response

	def by_key(self, response):
		data = response if isinstance(response, dict) else response.data
		return {item["substance_key"]: item for item in data["results"]}

	def record_names(self, item):
		return [r["name"] for r in item["records"]]


class ShapeTests(DrugFixture):
	def test_gilenya_at_both_regulators_is_one_item_with_tascenso(self):
		item = self.by_key(self.drugs("page_size=100"))["fingolimod"]
		self.assertEqual(item["name"], "Gilenya")
		self.assertEqual(item["substances"], ["fingolimod"])
		self.assertEqual(item["regulators"], ["ema", "fda"])
		self.assertEqual(item["first_authorised"], {"ema": "2011-03-17", "fda": "2010-09-21"})
		# EMA first, then the FDA's, oldest authorisation first.
		self.assertEqual(self.record_names(item), ["Gilenya", "GILENYA FDA", "TASCENSO ODT"])
		self.assertEqual(
			[r["id"] for r in item["records"]],
			[self.gilenya_ema.pk, self.gilenya_fda.pk, self.tascenso.pk],
		)
		self.assertEqual(
			set(item), {"substance_key", "name", "substances", "regulators", "first_authorised", "records"}
		)

	def test_a_record_is_the_row_of_medicines(self):
		item = self.by_key(self.drugs("page_size=100"))["fingolimod"]
		flat = self.client.get(f"/medicines/?page_size=100&substance_key=fingolimod").data["results"]
		self.assertEqual(
			sorted(item["records"], key=lambda r: r["id"]), sorted(flat, key=lambda r: r["id"])
		)

	def test_an_fda_only_drug_gets_its_brand_in_normal_case(self):
		item = self.by_key(self.drugs("page_size=100"))["ozanimod"]
		self.assertEqual(item["name"], "Zeposia")
		self.assertEqual(item["regulators"], ["fda"])
		# The key, not the FDA record's salt name.
		self.assertEqual(item["substances"], ["ozanimod"])
		self.assertEqual(item["first_authorised"], {"ema": None, "fda": "2020-03-25"})

	def test_the_ema_name_wins_over_the_fda_brand(self):
		items = self.by_key(self.drugs("page_size=100"))
		self.assertEqual(items["ocrelizumab"]["name"], "Ocrevus")
		self.assertEqual(items["interferon beta-1a"]["name"], "Rebif")

	def test_a_cbers_mixed_case_brand_is_left_as_it_is(self):
		self._set_fda_brand(self.zeposia, ["Zeposia Mixed"])
		self.assertEqual(self.by_key(self.drugs("page_size=100"))["ozanimod"]["name"], "Zeposia Mixed")

	def _set_fda_brand(self, medicine, brands):
		Medicine.objects.filter(pk=medicine.pk).update(brand_names=brands)

	def test_an_fda_row_without_a_brand_falls_back_to_its_name(self):
		self._set_fda_brand(self.zeposia, [])
		self.assertEqual(self.by_key(self.drugs("page_size=100"))["ozanimod"]["name"], "Zeposia")

	def test_ocrevus_zunovo_joins_ocrevus_and_the_enzyme_is_dropped(self):
		item = self.by_key(self.drugs("page_size=100"))["ocrelizumab"]
		self.assertEqual(
			self.record_names(item), ["Ocrevus", "OCREVUS FDA", "OCREVUS ZUNOVO"]
		)

	def test_a_combination_has_its_substances_and_a_plus_key(self):
		item = self.by_key(self.drugs("page_size=100"))["amlodipine+valsartan"]
		self.assertEqual(item["name"], "Exforge")
		self.assertEqual(item["substances"], ["amlodipine besylate", "valsartan"])

	def test_a_record_without_substances_stands_alone_without_substances(self):
		item = self.by_key(self.drugs("page_size=100"))[f"medicine-{self.keyless.pk}"]
		self.assertEqual(item["substances"], [])
		self.assertEqual(item["name"], "Keyless")

	def test_first_authorised_is_the_earliest_per_regulator(self):
		item = self.by_key(self.drugs("page_size=100"))["ocrelizumab"]
		# EMA: 2018-01-08 (hidden twin and the copy are not shown, or later); FDA: 2017.
		self.assertEqual(item["first_authorised"], {"ema": "2018-01-08", "fda": "2017-03-28"})
		undated = self.by_key(self.drugs("page_size=100"))["zzzumab"]
		self.assertEqual(undated["first_authorised"], {"ema": None, "fda": None})


class PagingTests(DrugFixture):
	def all_keys(self):
		return [i["substance_key"] for i in self.drugs("page_size=100").data["results"]]

	def test_count_counts_drugs_not_records(self):
		response = self.drugs("page_size=100")
		keys = self.all_keys()
		self.assertEqual(response.data["count"], len(keys))
		self.assertEqual(len(keys), len(set(keys)))
		records = sum(len(i["records"]) for i in response.data["results"])
		self.assertGreater(records, len(keys))
		# What /medicines/ counts for the same filters is the records.
		self.assertEqual(self.client.get("/medicines/?page_size=100").data["count"], records)

	def test_page_size_is_respected_and_pages_walk_every_drug_once(self):
		keys, url = [], f"{DRUGS}?page_size=4"
		first = self.client.get(url)
		self.assertEqual(first.data["page_size"], 4)
		self.assertIsNone(first.data["previous"])
		pages = 0
		response = first
		while True:
			pages += 1
			self.assertLessEqual(len(response.data["results"]), 4)
			keys += [i["substance_key"] for i in response.data["results"]]
			if not response.data["next"]:
				break
			response = self.client.get(response.data["next"])
			self.assertEqual(response.status_code, 200)
			self.assertTrue(response.data["previous"])
		self.assertEqual(pages, first.data["total_pages"])
		self.assertEqual(keys, self.all_keys())

	def test_a_drug_split_across_what_would_be_two_record_pages_stays_whole(self):
		# fingolimod has three records. Two records a page cuts it on /medicines/.
		flat = self.client.get("/medicines/?page_size=2&substance_key=fingolimod")
		self.assertEqual(len(flat.data["results"]), 2)
		self.assertTrue(flat.data["next"])
		by_drug = self.by_key(self.drugs("page_size=2&substance_key=fingolimod"))
		self.assertEqual(len(by_drug["fingolimod"]["records"]), 3)

	def test_the_previous_link_returns_to_the_same_drugs(self):
		second = self.client.get(f"{DRUGS}?page_size=3&page=2")
		back = self.client.get(second.data["previous"])
		self.assertEqual(
			[i["substance_key"] for i in back.data["results"]],
			self.all_keys()[:3],
		)

	def test_a_page_past_the_end_is_a_404(self):
		self.assertEqual(self.client.get(f"{DRUGS}?page_size=100&page=9").status_code, 404)

	def test_all_results_is_ignored(self):
		response = self.drugs("all_results=true&page_size=2")
		self.assertEqual(len(response.data["results"]), 2)

	def test_csv_is_not_offered(self):
		self.assertEqual(self.client.get(f"{DRUGS}?format=csv").status_code, 404)


class OrderingTests(DrugFixture):
	def keys(self, query):
		return [i["substance_key"] for i in self.drugs(f"page_size=100&{query}").data["results"]]

	def latest(self, item):
		dates = [r["authorisation_date"] for r in item["records"] if r["authorisation_date"]]
		return max(dates) if dates else None

	def test_the_default_is_the_newest_authorisation_first(self):
		items = self.drugs("page_size=100").data["results"]
		dated = [self.latest(i) for i in items if self.latest(i)]
		self.assertEqual(dated, sorted(dated, reverse=True))
		# The first is Ocrevus Zunovo (2024-09-13), through the shared ocrelizumab key.
		self.assertEqual(items[0]["substance_key"], "ocrelizumab")
		# Undated drugs last.
		self.assertIsNone(self.latest(items[-1]))
		self.assertEqual(items[-1]["substance_key"], "zzzumab")

	def test_explicit_descending_is_the_default(self):
		self.assertEqual(self.keys("ordering=-latest_authorisation"), self.keys(""))

	def test_ascending_puts_the_oldest_first_and_the_undated_still_last(self):
		keys = self.keys("ordering=latest_authorisation")
		items = {i["substance_key"]: i for i in self.drugs("page_size=100").data["results"]}
		self.assertEqual(keys[0], "interferon beta-1a")  # Avonex 1996, Rebif 1998
		self.assertEqual(keys[-1], "zzzumab")
		dated = [self.latest(items[k]) for k in keys if self.latest(items[k])]
		self.assertEqual(dated, sorted(dated))

	def test_name_is_alphabetical_on_the_name_shown_whatever_the_case(self):
		items = self.drugs("page_size=100&ordering=name").data["results"]
		names = [i["name"].lower() for i in items]
		self.assertEqual(names, sorted(names))
		self.assertIn("Zeposia", [i["name"] for i in items])
		# "Zeposia" (FDA, shown in normal case) sorts by "zeposia", before "Zzz undated".
		shown = [i["name"] for i in items]
		self.assertLess(shown.index("Zeposia"), shown.index("Zzz undated"))

	def test_descending_name(self):
		names = [i["name"].lower() for i in self.drugs("page_size=100&ordering=-name").data["results"]]
		self.assertEqual(names, sorted(names, reverse=True))

	def test_name_ordering_pages_do_not_repeat_or_skip(self):
		walked = []
		response = self.client.get(f"{DRUGS}?page_size=3&ordering=name")
		while True:
			walked += [i["substance_key"] for i in response.data["results"]]
			if not response.data["next"]:
				break
			response = self.client.get(response.data["next"])
		self.assertEqual(walked, self.keys("ordering=name"))

	def test_an_unknown_ordering_is_ignored(self):
		self.assertEqual(self.keys("ordering=bogus"), self.keys(""))
		self.assertEqual(self.keys("ordering=authorisation_date"), self.keys(""))

	def test_name_uses_the_records_shown(self):
		# regulator=fda: "Gilenya" is the FDA brand now, in normal case.
		items = self.drugs("page_size=100&regulator=fda&ordering=name").data["results"]
		names = [i["name"] for i in items]
		self.assertIn("Gilenya Fda", names)
		self.assertEqual([n.lower() for n in names], sorted(n.lower() for n in names))


class FilterTests(DrugFixture):
	def test_regulator_narrows_the_records_and_drops_drugs_with_none(self):
		items = self.by_key(self.drugs("page_size=100&regulator=fda"))
		self.assertEqual(self.record_names(items["fingolimod"]), ["GILENYA FDA", "TASCENSO ODT"])
		self.assertEqual(items["fingolimod"]["regulators"], ["fda"])
		self.assertEqual(items["fingolimod"]["first_authorised"], {"ema": None, "fda": "2010-09-21"})
		# Exforge has only an EMA record.
		self.assertNotIn("amlodipine+valsartan", items)
		ema = self.by_key(self.drugs("page_size=100&regulator=ema"))
		self.assertNotIn("ozanimod", ema)
		self.assertEqual(self.record_names(ema["fingolimod"]), ["Gilenya"])

	def test_the_count_follows_the_filters(self):
		response = self.drugs("page_size=100&regulator=fda")
		self.assertEqual(response.data["count"], len(response.data["results"]))
		self.assertLess(response.data["count"], self.drugs("page_size=100").data["count"])

	def test_subject_id_lists_only_the_records_for_that_subject(self):
		items = self.by_key(self.drugs(f"page_size=100&subject_id={self.s_als.pk}"))
		# The FDA's Ocrevus is linked to C_both, which has ALS; the EMA one is not.
		self.assertEqual(set(items), {"riluzole", "crossium", "halfium", "ocrelizumab"})
		self.assertEqual(self.record_names(items["ocrelizumab"]), ["OCREVUS FDA"])
		# `multi` and `als_only` both reach ALS through C_als.
		# Neither has a date: they follow in id order.
		self.assertEqual(self.record_names(items["riluzole"]), ["Riluzole Zentiva", "Exservan"])

	def test_a_combined_link_filter_holds_on_one_link(self):
		items = self.by_key(
			self.drugs(f"page_size=100&subject_id={self.s_als.pk}&category_id={self.c_ms.pk}")
		)
		# Only `multi` and `cross` are in C_ms and the category has ALS: via C_both? No:
		# C_ms has only MS, so nothing matches both.
		self.assertEqual(items, {})

	def test_tracked(self):
		untracked = self.by_key(self.drugs("page_size=100&tracked=false"))
		self.assertEqual(self.record_names(untracked["ofatumumab"]), ["Kesimpta untracked"])
		self.assertEqual(set(untracked), {"ofatumumab"})
		tracked = self.by_key(self.drugs("page_size=100&tracked=true"))
		self.assertEqual(self.record_names(tracked["ofatumumab"]), ["Kesimpta"])
		self.assertEqual(tracked["ofatumumab"]["records"][0]["tracked"], True)

	def test_generics_biosimilars_and_copies_are_left_out_by_default(self):
		items = self.by_key(self.drugs("page_size=100"))
		# A drug that is only generic or biosimilar is not listed.
		self.assertNotIn("natalizumab", items)
		self.assertNotIn("dimethyl fumarate", items)
		# A duplicate of Ocrevus stays out of Ocrevus.
		self.assertNotIn("Ocrevus copy", self.record_names(items["ocrelizumab"]))

	def test_include_generics_adds_them(self):
		items = self.by_key(self.drugs("page_size=100&include_generics=true"))
		self.assertEqual(
			self.record_names(items["natalizumab"]), ["Tyruko", "TYRUKO FDA"]
		)
		self.assertIn("dimethyl fumarate", items)
		self.assertIn("Ocrevus copy", self.record_names(items["ocrelizumab"]))
		# Still never the invisible twin.
		self.assertNotIn("Ocrevus hidden twin", self.record_names(items["ocrelizumab"]))

	def test_a_drug_of_copies_only_takes_the_name_of_an_ema_copy(self):
		# Everything else of Ocrevus is filtered out; the copy remains and gives the name.
		items = self.by_key(
			self.drugs(f"page_size=100&include_generics=true&name=copy&regulator=ema")
		)
		self.assertEqual(items["ocrelizumab"]["name"], "Ocrevus copy")
		ordered = self.drugs("page_size=100&include_generics=true&name=copy&ordering=name")
		self.assertEqual([i["name"] for i in ordered.data["results"]], ["Ocrevus copy"])

	def test_name_substance_and_date_filters_apply_to_records(self):
		items = self.by_key(self.drugs("page_size=100&name=tascenso"))
		self.assertEqual(set(items), {"fingolimod"})
		self.assertEqual(self.record_names(items["fingolimod"]), ["TASCENSO ODT"])
		self.assertEqual(
			self.record_names(self.by_key(self.drugs("page_size=100&substance=fingolimod hydrochloride"))["fingolimod"]),
			["Gilenya", "GILENYA FDA", "TASCENSO ODT"],
		)
		recent = self.by_key(self.drugs("page_size=100&authorised_after=2021-01-01"))
		self.assertEqual(self.record_names(recent["fingolimod"]), ["TASCENSO ODT"])

	def test_substance_key_filter(self):
		response = self.drugs("page_size=100&substance_key=fingolimod")
		self.assertEqual([i["substance_key"] for i in response.data["results"]], ["fingolimod"])
		self.assertEqual(response.data["count"], 1)

	def test_an_invalid_filter_is_a_400(self):
		self.assertEqual(self.client.get(f"{DRUGS}?regulator=nope").status_code, 400)
		self.assertEqual(self.client.get(f"{DRUGS}?authorised_after=yesterday").status_code, 400)


class VisibilityTests(DrugFixture):
	def test_a_record_the_caller_cannot_see_is_never_inside_a_drug(self):
		visible = {
			r["id"]
			for r in self.client.get("/medicines/?page_size=100&include_generics=true").data["results"]
		}
		items = self.drugs("page_size=100&include_generics=true").data["results"]
		shown = {r["id"] for item in items for r in item["records"]}
		self.assertEqual(shown, visible)
		self.assertNotIn(self.ocrevus_hidden.pk, shown)
		self.assertNotIn(self.hidden.pk, shown)
		self.assertNotIn(self.suppressed.pk, shown)

	def test_a_drug_with_no_visible_record_is_404_on_detail_and_absent_from_the_list(self):
		self.assertNotIn("hiddenium", self.by_key(self.drugs("page_size=100")))
		self.assertEqual(self.client.get(f"{DRUGS}hiddenium/").status_code, 404)
		self.assertEqual(self.client.get(f"{DRUGS}suppressium/").status_code, 404)

	def test_a_drug_with_one_visible_and_one_invisible_record_shows_only_the_visible(self):
		response = self.client.get(f"{DRUGS}ocrelizumab/")
		self.assertEqual(response.status_code, 200)
		self.assertNotIn("Ocrevus hidden twin", self.record_names(response.data))

	def test_a_site_with_medicines_off_sees_nothing(self):
		key = self.key_for(self.site_off)
		response = self.client.get(DRUGS, HTTP_AUTHORIZATION=key.api_key)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data["results"], [])
		self.assertEqual(response.data["count"], 0)
		detail = self.client.get(f"{DRUGS}fingolimod/", HTTP_AUTHORIZATION=key.api_key)
		self.assertEqual(detail.status_code, 404)

	def test_the_count_is_not_shared_with_a_caller_whose_medicine_scope_differs(self):
		from rest_framework.test import APIClient

		from api.tests.visibility_helpers import private_site_publishing

		same_subjects = private_site_publishing(
			self.s_ms, self.s_als, organization=self.org, domain="drugs-same.example.com",
			medicines_enabled=False,
		)
		key = self.key_for(same_subjects)
		cached = APIClient()
		anonymous = cached.get(DRUGS).data["count"]
		self.assertGreater(anonymous, 0)
		self.assertEqual(cached.get(DRUGS, HTTP_AUTHORIZATION=key.api_key).data["count"], 0)
		self.assertEqual(cached.get(DRUGS).data["count"], anonymous)

	def test_a_key_for_a_site_with_medicines_sees_the_same_drugs(self):
		key = self.key_for(self.site_on)
		self.assertEqual(
			self.drugs("page_size=100", HTTP_AUTHORIZATION=key.api_key).data["results"],
			self.drugs("page_size=100").data["results"],
		)

	def test_writes_are_refused(self):
		self.assertIn(self.client.post(DRUGS).status_code, (401, 403))
		self.assertIn(self.client.delete(f"{DRUGS}fingolimod/").status_code, (401, 403))


class DetailTests(DrugFixture):
	def test_the_detail_is_the_list_item(self):
		listed = self.by_key(self.client.get(f"{DRUGS}?page_size=100").json())
		detail = self.client.get(f"{DRUGS}fingolimod/")
		self.assertEqual(detail.status_code, 200)
		self.assertEqual(detail.json(), listed["fingolimod"])

	def test_a_key_with_a_space_url_encoded(self):
		response = self.client.get(f"{DRUGS}interferon%20beta-1a/")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data["substance_key"], "interferon beta-1a")
		self.assertEqual(self.record_names(response.data), ["Rebif", "AVONEX FDA"])

	def test_a_combination_key_with_the_plus_encoded(self):
		response = self.client.get(f"{DRUGS}amlodipine%2Bvalsartan/")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data["substance_key"], "amlodipine+valsartan")
		self.assertEqual(response.data["name"], "Exforge")

	def test_a_literal_plus_in_the_path_is_a_plus(self):
		# A path is not a query string: `+` is not a space here.
		response = self.client.get(f"{DRUGS}amlodipine+valsartan/")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data["substance_key"], "amlodipine+valsartan")

	def test_a_key_with_brackets(self):
		response = self.client.get(f"{DRUGS}hyaluronidase%20%28human%20recombinant%29/?include_generics=true")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(self.record_names(response.data), ["Enzyme alone"])
		literal = self.client.get(f"{DRUGS}hyaluronidase (human recombinant)/?include_generics=true")
		self.assertEqual(literal.status_code, 200)

	def test_a_key_with_a_dot(self):
		response = self.client.get(f"{DRUGS}compound%201.5/")
		self.assertEqual(response.status_code, 200, response.content)
		self.assertEqual(response.data["substance_key"], "compound 1.5")

	def test_a_name_that_reduces_to_a_key_redirects_to_it_keeping_the_query(self):
		cases = [
			("fingolimod%20hydrochloride", "fingolimod"),
			("dalfampridine", "fampridine"),
			("valsartan+amlodipine", "amlodipine%2Bvalsartan"),
			("Ocrelizumab+Hyaluronidase", "ocrelizumab"),
		]
		for given, canonical in cases:
			with self.subTest(given=given):
				response = self.client.get(f"{DRUGS}{given}/?include_generics=true&page_size=5")
				self.assertEqual(response.status_code, 301)
				self.assertEqual(
					response["Location"], f"{DRUGS}{canonical}/?include_generics=true&page_size=5"
				)
				followed = self.client.get(response["Location"])
				self.assertEqual(followed.status_code, 200)
				direct = self.client.get(f"{DRUGS}{canonical}/?include_generics=true&page_size=5")
				self.assertEqual(followed.json(), direct.json())

	def test_a_redirect_without_a_query_string_has_none(self):
		response = self.client.get(f"{DRUGS}fingolimod%20hydrochloride/")
		self.assertEqual(response.status_code, 301)
		self.assertEqual(response["Location"], f"{DRUGS}fingolimod/")

	def test_a_canonical_key_does_not_redirect(self):
		for key in ("fingolimod", "amlodipine%2Bvalsartan", "interferon%20beta-1a"):
			with self.subTest(key=key):
				self.assertEqual(self.client.get(f"{DRUGS}{key}/").status_code, 200)

	def test_an_unknown_name_still_404s_and_so_does_one_that_reduces_to_nothing_visible(self):
		self.assertEqual(self.client.get(f"{DRUGS}nothing-here%20hydrochloride/").status_code, 404)
		# The canonical drug exists but the caller cannot see it: no redirect to give it away.
		self.assertEqual(self.client.get(f"{DRUGS}hiddenium%20hydrochloride/").status_code, 404)
		# ...or it does not pass the filters.
		self.assertEqual(
			self.client.get(f"{DRUGS}valsartan+amlodipine/?regulator=fda").status_code, 404
		)
		# ...or it is a drug of generics and the default hides them.
		self.assertEqual(self.client.get(f"{DRUGS}natalizumab%20hydrochloride/").status_code, 404)

	def test_a_standalone_key_is_not_canonicalised(self):
		self.assertEqual(self.client.get(f"{DRUGS}medicine-99999999/").status_code, 404)
		self.assertEqual(self.client.get(f"{DRUGS}medicine-{self.keyless.pk}/").status_code, 200)

	def test_a_miss_costs_one_extra_query(self):
		def queries(url):
			self.client.get(url)
			cache.clear()
			with CaptureQueriesContext(connection) as q:
				self.client.get(url)
			return len(q)

		hit = queries(f"{DRUGS}fingolimod/")
		miss = queries(f"{DRUGS}nothing-here/")
		redirect = queries(f"{DRUGS}fingolimod%20hydrochloride/")
		self.assertLessEqual(miss, hit)
		self.assertLessEqual(redirect, hit)

	def test_a_format_suffix_still_works_without_the_trailing_slash(self):
		# `[^/]+` takes dots, so the router's `.json` route is what reads the suffix; the
		# slash form is always the key as written.
		response = self.client.get(f"{DRUGS}fingolimod.json")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()["substance_key"], "fingolimod")

	def test_the_key_is_case_insensitive(self):
		self.assertEqual(self.client.get(f"{DRUGS}Fingolimod/").data["substance_key"], "fingolimod")

	def test_an_unknown_key_is_a_404(self):
		self.assertEqual(self.client.get(f"{DRUGS}nothing-here/").status_code, 404)
		# A salt name is not a key: it redirects to the drug's (see below).
		self.assertEqual(self.client.get(f"{DRUGS}fingolimod%20hydrochloride/").status_code, 301)

	def test_the_filters_apply_to_the_detail(self):
		response = self.client.get(f"{DRUGS}fingolimod/?regulator=fda")
		self.assertEqual(self.record_names(response.data), ["GILENYA FDA", "TASCENSO ODT"])
		self.assertEqual(self.client.get(f"{DRUGS}amlodipine%2Bvalsartan/?regulator=fda").status_code, 404)

	def test_a_drug_of_generics_only_needs_include_generics(self):
		self.assertEqual(self.client.get(f"{DRUGS}natalizumab/").status_code, 404)
		response = self.client.get(f"{DRUGS}natalizumab/?include_generics=true")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(self.record_names(response.data), ["Tyruko", "TYRUKO FDA"])


class QueryCountTests(DrugFixture):
	def count_queries(self, url):
		self.client.get(url)
		cache.clear()
		with CaptureQueriesContext(connection) as queries:
			response = self.client.get(url)
		self.assertEqual(response.status_code, 200)
		return len(queries), response

	def test_the_list_costs_the_same_however_many_drugs_and_records(self):
		url = f"{DRUGS}?page_size=100"
		small, response = self.count_queries(url)
		drugs = len(response.data["results"])
		for i in range(6):
			ema = self._keyed(Regulator.EMA, f"Bulk EMA {i}", [f"bulkumab{i}"], self.c_ms)
			self._keyed(Regulator.FDA, f"BULK FDA {i}", [f"bulkumab{i} hydrochloride"], self.c_ms)
			self._keyed(Regulator.FDA, f"BULK TWO {i}", [f"bulkumab{i}"], self.c_als)
		large, response = self.count_queries(url)
		self.assertEqual(len(response.data["results"]), drugs + 6)
		self.assertEqual(small, large)
		self.assertLessEqual(large, 12)
		# The prefetches reached every record: categories on each, no extra queries.
		self.assertTrue(all(r["categories"] for i in response.data["results"] for r in i["records"] if r["tracked"]))

	def test_ordering_by_name_costs_the_same(self):
		url = f"{DRUGS}?page_size=100&ordering=name"
		small, _ = self.count_queries(url)
		for i in range(4):
			self._keyed(Regulator.FDA, f"MORE FDA {i}", [f"moreumab{i}"], self.c_ms)
		large, _ = self.count_queries(url)
		self.assertEqual(small, large)

	def test_a_filtered_list_costs_the_same(self):
		url = f"{DRUGS}?page_size=100&subject_id={self.s_ms.pk}&category_id={self.c_ms.pk}&regulator=fda"
		small, _ = self.count_queries(url)
		for i in range(4):
			self._keyed(Regulator.FDA, f"SUBJ FDA {i}", [f"subjumab{i}"], self.c_ms)
		large, _ = self.count_queries(url)
		self.assertEqual(small, large)

	def test_the_detail_costs_a_fixed_number(self):
		small, _ = self.count_queries(f"{DRUGS}fingolimod/")
		for i in range(4):
			self._keyed(Regulator.FDA, f"GILENYA MORE {i}", ["fingolimod hydrochloride"], self.c_ms)
		large, response = self.count_queries(f"{DRUGS}fingolimod/")
		self.assertEqual(len(response.data["records"]), 7)
		self.assertEqual(small, large)
