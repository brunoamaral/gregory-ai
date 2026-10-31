"""
``substance_key`` on /medicines/ and /medicines/events/, its filters, and the salt-tolerant
``substance`` filter of /medicines/.

Builds on the FDA fixture of test_medicines_api: Ocrevus at EMA and the FDA, every row
linked to categories the site shows. The keys are set here by hand, as the syncs would.
"""

from datetime import date, timedelta

from api.serializers import MedicineSerializer
from api.tests.test_medicines_api import FdaFixture, _ids
from gregory.models import Medicine, MedicineEvent, Regulator
from gregory.utils.substance_key import substance_key

Kind = MedicineEvent.Kind
Basis = MedicineEvent.Basis


class SubstanceKeyFixture(FdaFixture):
	@classmethod
	def _keyed(cls, regulator, name, substances, *categories, **fields):
		if regulator == Regulator.FDA:
			medicine = cls._fda(
				f"NDA9{cls.counter:05d}", name, substances[0], *categories, substances=substances, **fields
			)
		else:
			medicine = cls._medicine(name, substances[0], *categories, substances=substances, **fields)
		cls.counter += 1
		Medicine.objects.filter(pk=medicine.pk).update(substance_key=substance_key(substances))
		medicine.refresh_from_db()
		return medicine

	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		# The base fixtures' Ocrevus rows, keyed as the syncs would.
		Medicine.objects.filter(pk__in=[cls.ocrevus.pk, cls.fda.pk]).update(substance_key="ocrelizumab")
		Medicine.objects.filter(pk=cls.kesimpta.pk).update(substance_key="ofatumumab")

		cls.gilenya_ema = cls._keyed(Regulator.EMA, "Gilenya", ["fingolimod"], cls.c_ms)
		cls.gilenya_fda = cls._keyed(
			Regulator.FDA, "GILENYA FDA", ["fingolimod hydrochloride"], cls.c_ms,
			authorisation_date=date(2010, 9, 21),
		)
		cls.tascenso = cls._keyed(
			Regulator.FDA, "TASCENSO ODT", ["fingolimod lauryl sulfate"], cls.c_ms
		)
		cls.exforge = cls._keyed(
			Regulator.EMA, "Exforge", ["amlodipine besylate", "valsartan"], cls.c_ms
		)
		cls.rebif = cls._keyed(Regulator.EMA, "Rebif", ["interferon beta-1a"], cls.c_ms)
		cls.avonex_fda = cls._keyed(Regulator.FDA, "AVONEX FDA", ["interferon beta-1a"], cls.c_ms)
		cls.zunovo = cls._keyed(
			Regulator.FDA, "OCREVUS ZUNOVO", ["ocrelizumab", "hyaluronidase"], cls.c_ms
		)
		cls.enzyme_alone = cls._keyed(
			Regulator.EMA, "Enzyme alone", ["hyaluronidase (human recombinant) sodium"], cls.c_ms
		)
		cls.saline = cls._keyed(Regulator.EMA, "Saline", ["sodium chloride"], cls.c_ms)
		cls.ev_gilenya = cls._event(
			cls.gilenya_fda, Kind.FDA_APPROVED, cls.today - timedelta(days=3), Basis.OBSERVED
		)
		cls.ev_gilenya_ema = cls._event(
			cls.gilenya_ema, Kind.AUTHORISED, cls.today - timedelta(days=4), Basis.OBSERVED
		)
		cls.ev_exforge = cls._event(
			cls.exforge, Kind.AUTHORISED, cls.today - timedelta(days=5), Basis.OBSERVED
		)

	def found(self, query, path="/medicines/"):
		response = self.client.get(f"{path}?page_size=100&include_generics=true&{query}")
		self.assertEqual(response.status_code, 200, response.content)
		return sorted(r["name"] for r in response.data["results"])


class SubstanceKeyFieldTests(SubstanceKeyFixture):
	def test_the_key_is_on_every_row_and_the_detail(self):
		response = self.client.get("/medicines/?page_size=100&include_generics=true")
		self.assertTrue(response.data["results"])
		for row in response.data["results"]:
			self.assertIn("substance_key", row)
		rows = {r["name"]: r for r in response.data["results"]}
		self.assertEqual(rows["Gilenya"]["substance_key"], "fingolimod")
		self.assertEqual(rows["GILENYA FDA"]["substance_key"], "fingolimod")
		self.assertEqual(rows["Exforge"]["substance_key"], "amlodipine+valsartan")
		detail = self.client.get(f"/medicines/{self.gilenya_fda.pk}/")
		self.assertEqual(detail.data["substance_key"], "fingolimod")

	def test_the_key_is_on_the_medicine_of_an_event(self):
		response = self.client.get("/medicines/events/?page_size=100")
		keys = {r["medicine"]["name"]: r["medicine"]["substance_key"] for r in response.data["results"]}
		self.assertEqual(keys["GILENYA FDA"], "fingolimod")
		self.assertEqual(keys["Exforge"], "amlodipine+valsartan")

	def test_the_key_is_read_only(self):
		fields = MedicineSerializer().fields
		self.assertTrue(fields["substance_key"].read_only)


class SubstanceKeyFilterTests(SubstanceKeyFixture):
	def test_one_drug_at_both_regulators_and_its_other_applications(self):
		self.assertEqual(
			self.found("substance_key=fingolimod"), ["GILENYA FDA", "Gilenya", "TASCENSO ODT"]
		)

	def test_the_key_matches_exactly_and_ignores_case_and_spaces(self):
		self.assertEqual(
			self.found("substance_key=FINGOLIMOD"), ["GILENYA FDA", "Gilenya", "TASCENSO ODT"]
		)
		self.assertEqual(self.found("substance_key=%20fingolimod%20"), self.found("substance_key=fingolimod"))
		self.assertEqual(self.found("substance_key=fingolim"), [])
		self.assertEqual(self.found("substance_key=fingolimod%20hydrochloride"), [])

	def test_a_combination_key_needs_its_plus_encoded(self):
		self.assertEqual(self.found("substance_key=amlodipine%2Bvalsartan"), ["Exforge"])
		self.assertEqual(self.found("substance_key=valsartan"), [])

	def test_a_key_with_a_hyphen_and_the_enzyme_rule(self):
		self.assertEqual(self.found("substance_key=interferon%20beta-1a"), ["AVONEX FDA", "Rebif"])
		self.assertEqual(
			self.found("substance_key=ocrelizumab"),
			["OCREVUS FDA", "OCREVUS ZUNOVO", "Ocrevus"],
		)

	def test_it_combines_with_the_other_filters(self):
		self.assertEqual(self.found("substance_key=fingolimod&regulator=fda"), ["GILENYA FDA", "TASCENSO ODT"])
		self.assertEqual(self.found("substance_key=fingolimod&regulator=ema"), ["Gilenya"])
		self.assertEqual(self.found("substance_key=fingolimod&name=tascenso"), ["TASCENSO ODT"])

	def test_an_empty_value_filters_nothing(self):
		self.assertEqual(self.found("substance_key="), self.found(""))

	def test_the_events_filter_follows_the_medicine(self):
		response = self.client.get("/medicines/events/?page_size=100&substance_key=fingolimod")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(
			sorted(_ids(response)), sorted([self.ev_gilenya.pk, self.ev_gilenya_ema.pk])
		)
		response = self.client.get("/medicines/events/?page_size=100&substance_key=amlodipine%2Bvalsartan")
		self.assertEqual(_ids(response), [self.ev_exforge.pk])
		response = self.client.get("/medicines/events/?page_size=100&substance_key=nothing-like-this")
		self.assertEqual(_ids(response), [])

	def test_visibility_still_applies(self):
		# Hidden is linked only to a category outside the site's scope.
		hidden = self._keyed(Regulator.EMA, "Hidden Gilenya", ["fingolimod"], self.c_other)
		self.assertNotIn(hidden.name, self.found("substance_key=fingolimod"))


class SaltTolerantSubstanceTests(SubstanceKeyFixture):
	def test_the_bare_name_finds_the_salt_forms(self):
		self.assertEqual(
			self.found("substance=fingolimod"), ["GILENYA FDA", "Gilenya", "TASCENSO ODT"]
		)

	def test_the_salt_name_finds_the_bare_form(self):
		for query in (
			"substance=fingolimod%20hydrochloride",
			"substance=fingolimod%20lauryl%20sulfate",
			"substance=FINGOLIMOD%20Hydrochloride",
		):
			with self.subTest(query=query):
				self.assertEqual(
					self.found(query), ["GILENYA FDA", "Gilenya", "TASCENSO ODT"]
				)

	def test_a_combination_matches_on_one_part(self):
		self.assertEqual(self.found("substance=valsartan"), ["Exforge"])
		self.assertEqual(self.found("substance=amlodipine"), ["Exforge"])
		self.assertEqual(self.found("substance=amlodipine%20besylate"), ["Exforge"])
		# Not a part of the key: the two names run together.
		self.assertEqual(self.found("substance=amlodipine%20valsartan"), [])

	def test_a_part_is_matched_whole_not_as_a_prefix_or_suffix(self):
		self.assertEqual(self.found("substance=amlodip"), [])
		self.assertEqual(self.found("substance=sartan"), [])
		self.assertEqual(self.found("substance=ocrelizu"), [])

	def test_a_name_with_a_hyphen(self):
		self.assertEqual(self.found("substance=interferon%20beta-1a"), ["AVONEX FDA", "Rebif"])
		self.assertEqual(self.found("substance=interferon%20beta-1b"), [])

	def test_a_name_with_brackets_and_spaces_reaches_the_key_regex(self):
		# The record's substances do not overlap the value, so only the key's regex can match.
		self.assertEqual(self.enzyme_alone.substance_key, "hyaluronidase (human recombinant)")
		self.assertEqual(
			self.found("substance=hyaluronidase%20(human%20recombinant)"), ["Enzyme alone"]
		)

	def test_a_name_that_is_all_salt_stays_whole(self):
		self.assertEqual(self.found("substance=sodium%20chloride"), ["Saline"])
		self.assertEqual(self.found("substance=sodium"), [])

	def test_todays_exact_match_on_substances_still_works(self):
		# Zunovo's key drops the enzyme, so the enzyme is found by the substances route.
		self.assertEqual(self.found("substance=hyaluronidase"), ["OCREVUS ZUNOVO"])
		self.assertEqual(self.found("substance=OCRELIZUMAB"), ["OCREVUS FDA", "OCREVUS ZUNOVO", "Ocrevus"])

	def test_a_row_with_no_key_yet_still_matches_by_its_substances(self):
		Medicine.objects.filter(pk=self.rebif.pk).update(substance_key="")
		self.assertIn("Rebif", self.found("substance=interferon%20beta-1a"))

	def test_regex_characters_in_the_value_are_taken_literally(self):
		for value in ("a.b", "a*", "(", "[", "a%7Cb", "%5C", "%2B", "a%24", "%5E"):
			with self.subTest(value=value):
				self.assertEqual(self.found(f"substance={value}"), [])
		# A dot must not match any character.
		self.assertEqual(self.found("substance=fingol.mod"), [])

	def test_it_combines_with_regulator(self):
		self.assertEqual(self.found("substance=fingolimod&regulator=fda"), ["GILENYA FDA", "TASCENSO ODT"])
