"""The temporary gate that keeps FDA medicines out of every public read path.

The matcher links FDA medicines to categories from the labels and matching release on,
but the API, the feeds and the MCP tools (which read the API) still describe every row as
EMA's. Until the FDA release can label them, an FDA medicine, its events and its links are
hidden there; the admin is not gated. PR 3 removes the gate and these tests with it.

Built on the fixture of test_medicines_api.py: ``fda`` is an FDA application with a live
automatic link to C_ms (a category site ON shows), a subject link to S_ms, and events.
"""

from datetime import timedelta

from api.tests.test_editor_medicines import MedicineEditorFixture
from api.tests.test_medicines_api import (
	Basis,
	Kind,
	MedicineFixture,
	VISIBLE_BY_DEFAULT,
)
from gregory.models import (
	CategoryAssignmentSource,
	Medicine,
	MedicineCategoryAssignment,
	MedicineSubjectLink,
	Regulator,
)


class FdaGateFixture(MedicineFixture):
	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		cls.fda = Medicine.objects.create(
			regulator=Regulator.FDA,
			ema_product_number=None,
			fda_application_number="BLA761053",
			fda_application_type="BLA",
			fda_center="CDER",
			name="OCREVUS FDA",
			brand_names=["OCREVUS FDA"],
			substances=["ocrelizumab"],
			status="Approved",
			indication="OCREVUS is indicated for multiple sclerosis.",
			raw={},
		)
		cls._link(cls.fda, cls.c_ms)
		cls._link(cls.fda, cls.c_both)
		# The matcher never makes one, but the gate must not depend on that.
		MedicineSubjectLink.objects.create(medicine=cls.fda, subject=cls.s_als)
		cls.ev_fda = cls._event(
			cls.fda, Kind.FDA_EFFICACY_SUPPLEMENT, cls.today - timedelta(days=1), Basis.OBSERVED
		)


class ApiGateTests(FdaGateFixture):
	def test_the_automatic_fda_link_exists(self):
		link = MedicineCategoryAssignment.objects.get(medicine=self.fda, teamcategory=self.c_ms)
		self.assertEqual(link.source, CategoryAssignmentSource.AUTOMATIC)
		self.assertFalse(link.suppressed)

	def test_the_list_does_not_show_it(self):
		response = self.client.get("/medicines/?page_size=100")
		self.assertEqual(response.status_code, 200)
		self.assertEqual(self.names(response), sorted(VISIBLE_BY_DEFAULT))
		self.assertNotIn("OCREVUS FDA", self.names(response))

	def test_the_list_does_not_show_it_when_filtered_by_category_subject_or_site(self):
		for query in (
			f"category_id={self.c_ms.pk}",
			f"subject_id={self.s_ms.pk}",
			f"subject_id={self.s_als.pk}",
			"tracked=true",
			"tracked=false",
			"include_generics=true",
		):
			with self.subTest(query=query):
				response = self.client.get(f"/medicines/?page_size=100&{query}")
				self.assertEqual(response.status_code, 200)
				self.assertNotIn("OCREVUS FDA", self.names(response))

	def test_the_detail_is_a_404(self):
		self.assertEqual(self.client.get(f"/medicines/{self.fda.pk}/").status_code, 404)
		self.assertEqual(self.client.get(f"/medicines/{self.ocrevus.pk}/").status_code, 200)

	def test_the_events_do_not_show_it(self):
		response = self.client.get("/medicines/events/?page_size=100")
		self.assertEqual(response.status_code, 200)
		ids = {row["id"] for row in response.data["results"]}
		self.assertNotIn(self.ev_fda.pk, ids)
		self.assertIn(self.ev_auth.pk, ids)

	def test_medicines_count_does_not_count_it(self):
		response = self.client.get("/categories/")
		counts = {row["category_slug"]: row["medicines_count"] for row in response.data["results"]}
		# The same figures as without the FDA medicine (test_medicines_api).
		self.assertEqual(counts["ocrelizumab"], 5)
		self.assertEqual(counts["riluzole"], 4)
		self.assertEqual(counts["broad"], 0)

	def test_a_category_filter_agrees_with_the_count(self):
		counts = {
			row["category_slug"]: row["medicines_count"]
			for row in self.client.get("/categories/").data["results"]
		}
		listed = self.client.get(f"/medicines/?category_id={self.c_ms.pk}").data["count"]
		self.assertEqual(counts["ocrelizumab"], listed)

	def test_the_admin_still_has_it(self):
		# The gate is on the public read paths only.
		self.assertTrue(Medicine.objects.filter(pk=self.fda.pk, regulator=Regulator.FDA).exists())
		self.assertTrue(MedicineCategoryAssignment.objects.filter(medicine=self.fda).exists())


class EditorGateTests(MedicineEditorFixture):
	def test_an_fda_medicine_is_not_found_by_the_editor_link_route(self):
		fda = Medicine.objects.create(
			regulator=Regulator.FDA,
			ema_product_number=None,
			fda_application_number="NDA022561",
			fda_application_type="NDA",
			name="MAVENCLAD",
			substances=["cladribine"],
			status="Approved",
			raw={},
		)
		self.make_link(fda, self.cat_a, CategoryAssignmentSource.AUTOMATIC)
		self.assertEqual(self.delete(self.cat_a, fda).status_code, 404)
		self.assertEqual(self.put(self.cat_a, fda).status_code, 404)
		self.assertTrue(MedicineCategoryAssignment.objects.get(medicine=fda).suppressed is False)

	def test_an_ema_medicine_is_unaffected(self):
		self.assertEqual(self.delete(self.cat_a, self.auto).status_code, 200)
