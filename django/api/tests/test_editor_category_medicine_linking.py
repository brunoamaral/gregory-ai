"""
Creating or changing a category through ``/editor/categories/`` (the MCP
``create_category`` and ``update_category`` tools) links its EMA medicines at once, so
the editor's change shows without waiting for the next pipeline run.

Same fixture as test_editor_categories: ana edits sites A and B, ben reads site A only.
Nothing calls EMA: the medicines are built directly.
"""

from unittest.mock import patch

from api.tests.test_editor_categories import CategoryFixture
from gregory.models import (
	CategoryAssignmentSource,
	CategoryModality,
	Medicine,
	MedicineCategoryAssignment,
	Subject,
	TeamCategory,
)
from sitesettings.models import CustomSetting

SOURCE = CategoryAssignmentSource
_counter = [0]


def _medicine(name, substance, **fields):
	_counter[0] += 1
	values = {
		"ema_product_number": f"EMEA/H/C/{800000 + _counter[0]}",
		"name": name,
		"inn": substance,
		"substances": [substance],
		"status": "Authorised",
		"raw": {},
	}
	values.update(fields)
	return Medicine.objects.create(**values)


class LinkOnWriteFixture(CategoryFixture):
	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		cls.apo = _medicine("Apotex", "apotransferrin")
		cls.lecanemab = _medicine("Leqembo", "lecanemab", therapeutic_areas=["Alzheimer Disease"])
		cls.diagnostic = _medicine("Imagix", "apotransferrin", atc_code="V09AX06")

	def create(self, **body):
		body.setdefault("modality", CategoryModality.BIOLOGIC_ANTIBODY)
		return super().create(**body)

	def patch(self, category, body, **kwargs):
		return self.send("patch", f"/editor/categories/{category.pk}/", body, **kwargs)

	def linked(self, category_id):
		return {
			link.medicine.name: link
			for link in MedicineCategoryAssignment.objects.filter(teamcategory_id=category_id).select_related("medicine")
		}


class CreateLinksMedicinesTest(LinkOnWriteFixture):
	def test_the_new_category_is_linked_in_the_same_request(self):
		response = self.create()
		self.assertEqual(response.status_code, 201, response.content)
		links = self.linked(response.json()["id"])
		self.assertEqual(set(links), {"Apotex"})
		self.assertEqual(links["Apotex"].source, SOURCE.AUTOMATIC)
		self.assertEqual(links["Apotex"].matched_on, "apotransferrin")

	def test_the_links_are_pipeline_writes_with_no_editor(self):
		category_id = self.create().json()["id"]
		link = MedicineCategoryAssignment.objects.get(teamcategory_id=category_id)
		self.assertFalse(MedicineCategoryAssignment.history.filter(id=link.pk).exists())

	def test_a_diagnostic_is_never_linked(self):
		links = self.linked(self.create().json()["id"])
		self.assertNotIn("Imagix", links)

	def test_the_subjects_ema_areas_still_apply(self):
		subject = Subject.objects.create(
			team=self.subject_a.team, subject_name="Alz", subject_slug="alz-linking",
			ema_therapeutic_areas=["Alzheimer Disease"],
		)
		CustomSetting.objects.get(site=self.site_a).scope_subjects.add(subject)
		on_alzheimers = self.create(
			category_name="Lecanemab", category_terms=["lecanemab"], subject_ids=[subject.pk]
		).json()["id"]
		on_other = self.create(
			category_name="Lecanemab elsewhere", category_terms=["lecanemab"], subject_ids=[self.subject_a.pk]
		).json()["id"]
		self.assertEqual(set(self.linked(on_alzheimers)), {"Leqembo"})
		self.assertEqual(set(self.linked(on_other)), {"Leqembo"})
		# An area the medicine isn't approved for rules it out.
		subject.ema_therapeutic_areas = ["Multiple Sclerosis"]
		subject.save()
		other = self.create(
			category_name="Lecanemab MS", category_terms=["lecanemab"], subject_ids=[subject.pk]
		).json()["id"]
		self.assertEqual(self.linked(other), {})

	def test_a_manual_category_is_not_linked(self):
		response = self.create(category_type="manual", category_terms=["apotransferrin"])
		self.assertEqual(response.status_code, 201, response.content)
		self.assertEqual(self.linked(response.json()["id"]), {})

	def test_a_research_topic_gets_no_automatic_medicines(self):
		response = self.create(modality=CategoryModality.RESEARCH_TOPIC)
		self.assertEqual(self.linked(response.json()["id"]), {})

	def test_a_matcher_failure_does_not_fail_the_request(self):
		with patch("api.editor_views.link_category", side_effect=RuntimeError("boom")):
			with self.assertLogs("api.editor_views", level="ERROR"):
				response = self.create()
		self.assertEqual(response.status_code, 201, response.content)
		category_id = response.json()["id"]
		self.assertTrue(TeamCategory.objects.filter(pk=category_id).exists())
		self.assertEqual(self.linked(category_id), {})

	def test_a_refused_create_links_nothing(self):
		with patch("api.editor_views.link_category") as linked:
			response = self.create(category_name="Cordycepin")
		self.assertEqual(response.status_code, 409)
		linked.assert_not_called()


class UpdateLinksMedicinesTest(LinkOnWriteFixture):
	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		cls.cat = TeamCategory.objects.create(
			team=cls.subject_a.team, category_name="Apo", category_slug="apo",
			category_terms=["apotransferrin"], modality=CategoryModality.BIOLOGIC_ANTIBODY,
		)
		cls.cat.subjects.add(cls.subject_a)

	def test_changing_the_terms_relinks_at_once(self):
		self.patch(self.cat, {"category_terms": ["lecanemab"]})
		self.assertEqual(set(self.linked(self.cat.pk)), {"Leqembo"})

	def test_adding_a_term_adds_its_medicines(self):
		self.patch(self.cat, {"add_terms": ["lecanemab"]})
		self.assertEqual(set(self.linked(self.cat.pk)), {"Apotex", "Leqembo"})

	def test_adding_an_excluded_term_removes_the_link(self):
		self.patch(self.cat, {"add_terms": ["lecanemab"]})
		self.patch(self.cat, {"add_exclude_terms": ["lecanemab"]})
		self.assertEqual(set(self.linked(self.cat.pk)), {"Apotex"})

	def test_changing_the_modality_relinks(self):
		self.patch(self.cat, {"modality": CategoryModality.RESEARCH_TOPIC})
		self.assertEqual(self.linked(self.cat.pk), {})
		self.patch(self.cat, {"modality": CategoryModality.SMALL_MOLECULE})
		self.assertEqual(set(self.linked(self.cat.pk)), {"Apotex"})

	def test_changing_the_subjects_applies_the_new_disease_check(self):
		self.patch(self.cat, {"add_terms": ["lecanemab"]})
		subject = Subject.objects.create(
			team=self.subject_a.team, subject_name="MS-ish", subject_slug="ms-ish-linking",
			ema_therapeutic_areas=["Multiple Sclerosis"],
		)
		CustomSetting.objects.get(site=self.site_a).scope_subjects.add(subject)
		self.patch(self.cat, {"subject_ids": [subject.pk]})
		# Neither medicine is approved for MS.
		self.assertEqual(self.linked(self.cat.pk), {})

	def test_a_hand_link_and_a_suppressed_link_are_left_alone(self):
		MedicineCategoryAssignment.objects.create(medicine=self.lecanemab, teamcategory=self.cat, source=SOURCE.MANUAL)
		MedicineCategoryAssignment.objects.create(
			medicine=self.apo, teamcategory=self.cat, source=SOURCE.AUTOMATIC, suppressed=True
		)
		self.patch(self.cat, {"add_terms": ["something else"]})
		links = self.linked(self.cat.pk)
		self.assertEqual(links["Leqembo"].source, SOURCE.MANUAL)
		self.assertTrue(links["Apotex"].suppressed)
		self.assertEqual(len(links), 2)

	def test_a_name_or_description_change_does_not_run_the_matcher(self):
		with patch("api.editor_views.link_category") as linked:
			self.patch(self.cat, {"category_name": "Apo renamed", "category_description": "New"})
		linked.assert_not_called()

	def test_a_request_that_changes_nothing_does_not_run_the_matcher(self):
		with patch("api.editor_views.link_category") as linked:
			response = self.patch(self.cat, {"category_terms": ["apotransferrin"], "modality": CategoryModality.BIOLOGIC_ANTIBODY})
		self.assertEqual(response.status_code, 200, response.content)
		linked.assert_not_called()

	def test_a_manual_category_is_not_linked(self):
		manual = TeamCategory.objects.create(
			team=self.subject_a.team, category_name="By hand", category_slug="by-hand",
			category_type="manual", category_terms=["apotransferrin"],
		)
		manual.subjects.add(self.subject_a)
		self.patch(manual, {"add_terms": ["lecanemab"]})
		self.assertEqual(self.linked(manual.pk), {})

	def test_a_matcher_failure_does_not_fail_the_request(self):
		with patch("api.editor_views.link_category", side_effect=RuntimeError("boom")):
			with self.assertLogs("api.editor_views", level="ERROR"):
				response = self.patch(self.cat, {"category_terms": ["lecanemab"]})
		self.assertEqual(response.status_code, 200, response.content)
		self.cat.refresh_from_db()
		self.assertEqual(self.cat.category_terms, ["lecanemab"])

	def test_a_refused_change_links_nothing(self):
		with patch("api.editor_views.link_category") as linked:
			response = self.patch(self.cat, {"category_terms": []})
		self.assertEqual(response.status_code, 400)
		linked.assert_not_called()

	def test_the_links_it_creates_record_no_history(self):
		self.patch(self.cat, {"category_terms": ["lecanemab"]})
		link = MedicineCategoryAssignment.objects.get(teamcategory=self.cat, medicine=self.lecanemab)
		self.assertFalse(MedicineCategoryAssignment.history.filter(id=link.pk).exists())
