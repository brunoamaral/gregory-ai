"""
The ``/editor/categories/<id>/medicines/<id>/`` route (api/editor_views.py
``EditorCategoryMedicineView``) and the editor's read of ``/editor/medicines/``.

Setup: one organisation with a public site A (subject A, medicines ON), a private
MCP-enabled site B (subject B, medicines OFF) and another organisation's site.
Editors hold grants on one site each. Medicines are reachable only through a site with
``medicines_enabled`` on, so every route is 404 for an editor of site B.

  categories: cat_a {A}, cat_a2 {A}, cat_b {B}, cat_mixed {A, B}, cat_other {other}
  medicines:  auto (automatic link to cat_a), hand (manual link to cat_a),
              orphan (no link at all), only_b (linked to cat_b only)

Nothing here calls EMA: medicines are built directly.
"""

from io import StringIO

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils.text import slugify
from rest_framework.test import APIClient

from api.tests.test_editor_patents import headers
from api.tests.visibility_helpers import private_site_publishing, publish_subjects
from gregory.models import (
	CategoryAssignmentSource,
	Medicine,
	MedicineCategoryAssignment,
	Regulator,
	Subject,
	Team,
	TeamCategory,
)
from mcpauth.tests.helpers import SERVICE_KEY, grant, make_org, make_user
from sitesettings.models import CustomSetting

SOURCE = CategoryAssignmentSource


def _subject(org, name):
	team = Team.objects.create(organization=org, name=f"{name} team", slug=slugify(f"{name} team"))
	return Subject.objects.create(team=team, subject_name=name, subject_slug=slugify(name))


def _category(subject, name, *more):
	category = TeamCategory.objects.create(
		team=subject.team, category_name=name, category_slug=slugify(name), category_terms=["zzzz"]
	)
	category.subjects.add(subject, *more)
	return category


_counter = [0]


def _medicine(name, inn, **fields):
	_counter[0] += 1
	values = {
		"ema_product_number": f"EMEA/H/C/{900000 + _counter[0]}",
		"name": name,
		"inn": inn,
		"substances": [inn],
		"status": "Authorised",
		"url": f"https://www.ema.europa.eu/en/medicines/human/EPAR/{slugify(name)}",
		"raw": {},
	}
	values.update(fields)
	return Medicine.objects.create(**values)


@override_settings(GREGORY_MCP_SERVICE_KEY=SERVICE_KEY)
class MedicineEditorFixture(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.org = make_org("Medicine Editor Org")
		cls.other_org = make_org("Medicine Editor Other Org")
		cls.subject_a = _subject(cls.org, "Medicine Subject A")
		cls.subject_b = _subject(cls.org, "Medicine Subject B")
		cls.subject_other = _subject(cls.other_org, "Medicine Subject Other")
		cls.site_a = publish_subjects(
			cls.subject_a, organization=cls.org, domain="me-a.example.com", medicines_enabled=True
		)
		# Medicines OFF: the editor of this site has no medicine scope at all.
		cls.site_b = private_site_publishing(
			cls.subject_b, organization=cls.org, domain="me-b.example.com", medicines_enabled=False
		)
		cls.site_other = publish_subjects(
			cls.subject_other, organization=cls.other_org, domain="me-o.example.com", medicines_enabled=True
		)
		CustomSetting.objects.filter(site__in=[cls.site_a, cls.site_b, cls.site_other]).update(mcp_enabled=True)

		cls.ana = make_user("me-ana", cls.org)
		cls.ben = make_user("me-ben", cls.org)
		grant(cls.ana, cls.site_a)
		grant(cls.ana, cls.site_b)
		grant(cls.ben, cls.site_a, can_edit=False)

		cls.cat_a = _category(cls.subject_a, "Med Cat A")
		cls.cat_a2 = _category(cls.subject_a, "Med Cat A2")
		cls.cat_b = _category(cls.subject_b, "Med Cat B")
		# Shown on site A and site B: site A may read it but not change it.
		cls.cat_mixed = _category(cls.subject_a, "Med Cat Mixed", cls.subject_b)
		cls.cat_other = _category(cls.subject_other, "Med Cat Other")

		cls.auto = _medicine("Autoprex", "autoprexumab")
		cls.hand = _medicine("Handprex", "handprexumab")
		cls.orphan = _medicine("Orphanex", "orphanexumab")
		cls.only_b = _medicine("Bonlyx", "bonlyxumab")
		cls.only_other = _medicine("Otherex", "otherexumab")
		cls.auto_link = cls.make_link(cls.auto, cls.cat_a, SOURCE.AUTOMATIC, matched_on="autoprexumab")
		cls.hand_link = cls.make_link(cls.hand, cls.cat_a, SOURCE.MANUAL)
		cls.make_link(cls.only_b, cls.cat_b, SOURCE.AUTOMATIC)
		cls.make_link(cls.only_other, cls.cat_other, SOURCE.AUTOMATIC)

	@staticmethod
	def make_link(medicine, category, source, **fields):
		return MedicineCategoryAssignment.objects.create(
			medicine=medicine, teamcategory=category, source=source, **fields
		)

	def setUp(self):
		cache.clear()
		self.client = APIClient()

	def path(self, category, medicine):
		return f"/editor/categories/{category.pk}/medicines/{medicine.pk}/"

	def get(self, path, user=None, site=None):
		return self.client.get(path, **headers(user or self.ana, site or self.site_a))

	def put(self, category, medicine, user=None, site=None):
		return self.client.put(self.path(category, medicine), **headers(user or self.ana, site or self.site_a))

	def delete(self, category, medicine, user=None, site=None):
		return self.client.delete(self.path(category, medicine), **headers(user or self.ana, site or self.site_a))

	def link(self, medicine, category):
		return MedicineCategoryAssignment.objects.filter(medicine=medicine, teamcategory=category).first()

	def history(self, medicine, category):
		return MedicineCategoryAssignment.history.filter(
			medicine_id=medicine.pk, teamcategory_id=category.pk
		).order_by("history_date", "history_id")


class AuthorisationTest(MedicineEditorFixture):
	def test_no_credential_is_401(self):
		response = self.client.put(self.path(self.cat_a, self.auto))
		self.assertEqual(response.status_code, 401)

	def test_read_only_editor_cannot_write(self):
		for call in (self.put, self.delete):
			response = call(self.cat_a, self.auto, user=self.ben)
			self.assertEqual(response.status_code, 403)
		self.assertFalse(self.link(self.auto, self.cat_a).suppressed)

	def test_user_without_a_grant_is_403(self):
		stranger = make_user("me-stranger", self.org)
		self.assertEqual(self.put(self.cat_a, self.auto, user=stranger).status_code, 403)


class SuppressAndRestoreTest(MedicineEditorFixture):
	def test_delete_suppresses_an_automatic_link(self):
		response = self.delete(self.cat_a, self.auto)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data["medicine_id"], self.auto.pk)
		self.assertEqual(response.data["category_id"], self.cat_a.pk)
		self.assertFalse(response.data["linked"])
		self.assertTrue(response.data["suppressed"])
		self.assertEqual(response.data["source"], "automatic")
		self.assertEqual(response.data["change"], "suppressed")
		link = self.link(self.auto, self.cat_a)
		self.assertTrue(link.suppressed)
		self.assertEqual(link.pk, self.auto_link.pk, "the row is kept so the matcher can see it")

	def test_put_restores_a_suppressed_link(self):
		self.delete(self.cat_a, self.auto)
		response = self.put(self.cat_a, self.auto)
		self.assertEqual(response.status_code, 200)
		self.assertTrue(response.data["linked"])
		self.assertFalse(response.data["suppressed"])
		self.assertEqual(response.data["source"], "automatic")
		self.assertEqual(response.data["change"], "restored")
		self.assertFalse(self.link(self.auto, self.cat_a).suppressed)

	def test_a_suppressed_link_is_hidden_from_the_api(self):
		self.assertEqual(self.get(f"/editor/medicines/{self.auto.pk}/").status_code, 200)
		self.delete(self.cat_a, self.auto)
		# The only live link is gone, so the medicine is out of the editor's list...
		self.assertEqual(self.get(f"/editor/medicines/{self.auto.pk}/").status_code, 404)
		# ...but the restore that undoes it still finds it.
		self.assertEqual(self.put(self.cat_a, self.auto).status_code, 200)
		self.assertEqual(self.get(f"/editor/medicines/{self.auto.pk}/").status_code, 200)

	def test_match_medicines_leaves_a_suppressed_link_and_a_hand_link_alone(self):
		# The category's terms match neither product, so the matcher would delete an
		# automatic link on its own; it must not touch a suppressed or a manual one.
		self.delete(self.cat_a, self.auto)
		call_command("match_medicines", category=self.cat_a.pk, stdout=StringIO())
		suppressed = self.link(self.auto, self.cat_a)
		self.assertIsNotNone(suppressed)
		self.assertTrue(suppressed.suppressed)
		self.assertIsNotNone(self.link(self.hand, self.cat_a))

	def test_repeating_either_call_is_unchanged_and_records_nothing(self):
		self.delete(self.cat_a, self.auto)
		count = self.history(self.auto, self.cat_a).count()
		again = self.delete(self.cat_a, self.auto)
		self.assertEqual(again.status_code, 200)
		self.assertEqual(again.data["change"], "unchanged")
		self.assertFalse(again.data["linked"])
		self.assertEqual(self.history(self.auto, self.cat_a).count(), count)

		self.put(self.cat_a, self.auto)
		count = self.history(self.auto, self.cat_a).count()
		again = self.put(self.cat_a, self.auto)
		self.assertEqual(again.status_code, 200)
		self.assertEqual(again.data["change"], "unchanged")
		self.assertTrue(again.data["linked"])
		self.assertEqual(self.history(self.auto, self.cat_a).count(), count)

	def test_put_on_a_live_automatic_link_leaves_it_automatic(self):
		response = self.put(self.cat_a, self.auto)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data["source"], "automatic")
		self.assertEqual(self.link(self.auto, self.cat_a).source, SOURCE.AUTOMATIC)


class ManualLinkTest(MedicineEditorFixture):
	def test_put_adds_a_manual_link_to_another_category(self):
		response = self.put(self.cat_a2, self.auto)
		self.assertEqual(response.status_code, 201)
		self.assertTrue(response.data["linked"])
		self.assertEqual(response.data["source"], "manual")
		self.assertEqual(response.data["change"], "added")
		link = self.link(self.auto, self.cat_a2)
		self.assertEqual(link.source, SOURCE.MANUAL)
		self.assertFalse(link.suppressed)
		self.assertEqual(link.matched_on, "")

	def test_adding_twice_is_one_link(self):
		self.put(self.cat_a2, self.auto)
		again = self.put(self.cat_a2, self.auto)
		self.assertEqual(again.status_code, 200)
		self.assertEqual(again.data["change"], "unchanged")
		self.assertEqual(MedicineCategoryAssignment.objects.filter(medicine=self.auto, teamcategory=self.cat_a2).count(), 1)

	def test_delete_removes_a_manual_link_outright(self):
		response = self.delete(self.cat_a, self.hand)
		self.assertEqual(response.status_code, 200)
		self.assertFalse(response.data["linked"])
		self.assertIsNone(response.data["source"])
		self.assertFalse(response.data["suppressed"])
		self.assertEqual(response.data["change"], "deleted")
		self.assertIsNone(self.link(self.hand, self.cat_a))

	def test_deleting_a_manual_link_twice_is_still_200(self):
		self.delete(self.cat_a, self.hand)
		again = self.delete(self.cat_a, self.hand)
		self.assertEqual(again.status_code, 200)
		self.assertEqual(again.data["change"], "unchanged")
		self.assertFalse(again.data["linked"])

	def test_a_deleted_manual_link_can_be_added_back(self):
		self.delete(self.cat_a, self.hand)
		response = self.put(self.cat_a, self.hand)
		self.assertEqual(response.status_code, 201)
		self.assertEqual(response.data["source"], "manual")

	def test_a_hand_link_survives_match_medicines(self):
		self.put(self.cat_a2, self.auto)
		call_command("match_medicines", stdout=StringIO())
		self.assertEqual(self.link(self.auto, self.cat_a2).source, SOURCE.MANUAL)

	def test_delete_of_a_pair_that_was_never_linked_is_an_unchanged_200(self):
		response = self.delete(self.cat_a2, self.auto)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.data["change"], "unchanged")
		self.assertIsNone(self.link(self.auto, self.cat_a2))


class HistoryTest(MedicineEditorFixture):
	def test_a_suppression_is_recorded_under_the_editor_via_mcp(self):
		self.delete(self.cat_a, self.auto)
		row = self.history(self.auto, self.cat_a).last()
		self.assertTrue(row.suppressed)
		self.assertEqual(row.history_type, "~")
		self.assertEqual(row.editor_user, self.ana)
		self.assertIn("me-ana", row.editor_label)
		self.assertEqual(row.via, "mcp")

	def test_a_restore_and_an_add_are_recorded(self):
		self.delete(self.cat_a, self.auto)
		self.put(self.cat_a, self.auto)
		self.put(self.cat_a2, self.auto)
		restore = self.history(self.auto, self.cat_a).last()
		self.assertFalse(restore.suppressed)
		self.assertEqual((restore.editor_user, restore.via), (self.ana, "mcp"))
		add = self.history(self.auto, self.cat_a2).get()
		self.assertEqual((add.history_type, add.source, add.editor_user, add.via), ("+", "manual", self.ana, "mcp"))

	def test_a_deletion_is_recorded_too(self):
		self.delete(self.cat_a, self.hand)
		row = self.history(self.hand, self.cat_a).last()
		self.assertEqual(row.history_type, "-")
		self.assertEqual((row.editor_user, row.via), (self.ana, "mcp"))

	def test_the_response_names_who_and_when(self):
		response = self.delete(self.cat_a, self.auto)
		self.assertIn("me-ana", response.data["updated_by"])
		self.assertIsNotNone(response.data["updated_at"])

	def test_each_row_is_saved_individually(self):
		# A queryset update() would write no history row at all.
		before = MedicineCategoryAssignment.history.count()
		self.delete(self.cat_a, self.auto)
		self.assertEqual(MedicineCategoryAssignment.history.count(), before + 1)


class ScopeTest(MedicineEditorFixture):
	def test_a_category_of_another_organisation_is_404_on_both_verbs(self):
		for call in (self.put, self.delete):
			self.assertEqual(call(self.cat_other, self.only_other).status_code, 404)
		self.assertFalse(self.link(self.only_other, self.cat_other).suppressed)

	def test_a_category_of_a_site_without_medicines_is_404(self):
		# cat_b is in site B's scope, not site A's.
		self.assertEqual(self.put(self.cat_b, self.only_b).status_code, 404)

	def test_a_category_shared_with_another_site_is_read_only_403(self):
		# category_for_edit: every subject must be in the editor's scope.
		for call in (self.put, self.delete):
			self.assertEqual(call(self.cat_mixed, self.auto).status_code, 403)
		self.assertIsNone(self.link(self.auto, self.cat_mixed))

	def test_a_medicine_the_site_cannot_see_is_404(self):
		# Linked only to a category outside site A's scope.
		for call in (self.put, self.delete):
			self.assertEqual(call(self.cat_a, self.only_other).status_code, 404)
		self.assertIsNone(self.link(self.only_other, self.cat_a))

	def test_a_medicine_with_no_link_anywhere_is_404(self):
		for call in (self.put, self.delete):
			self.assertEqual(call(self.cat_a, self.orphan).status_code, 404)
		self.assertIsNone(self.link(self.orphan, self.cat_a))

	def test_a_missing_medicine_or_category_is_404(self):
		missing = Medicine(pk=99999999)
		self.assertEqual(self.put(self.cat_a, missing).status_code, 404)
		self.assertEqual(self.client.put("/editor/categories/99999999/medicines/%d/" % self.auto.pk, **headers(self.ana, self.site_a)).status_code, 404)

	def test_a_medicine_linked_only_by_a_suppressed_link_is_still_reachable(self):
		self.make_link(self.orphan, self.cat_a2, SOURCE.AUTOMATIC, suppressed=True)
		self.assertEqual(self.put(self.cat_a, self.orphan).status_code, 201)

	def test_a_link_to_another_sites_category_does_not_make_a_medicine_visible(self):
		self.make_link(self.orphan, self.cat_other, SOURCE.AUTOMATIC)
		self.assertEqual(self.put(self.cat_a, self.orphan).status_code, 404)


class SiteWithoutMedicinesTest(MedicineEditorFixture):
	"""Ana also edits site B, which has ``medicines_enabled`` off."""

	def test_every_route_is_404(self):
		for call in (self.put, self.delete):
			response = call(self.cat_b, self.only_b, site=self.site_b)
			self.assertEqual(response.status_code, 404, response.data)
		self.assertFalse(self.link(self.only_b, self.cat_b).suppressed)

	def test_the_list_and_detail_are_empty(self):
		listing = self.get("/editor/medicines/", site=self.site_b)
		self.assertEqual(listing.status_code, 200)
		self.assertEqual(listing.data["count"], 0)
		self.assertEqual(self.get(f"/editor/medicines/{self.only_b.pk}/", site=self.site_b).status_code, 404)

	def test_turning_the_switch_on_opens_the_routes(self):
		CustomSetting.objects.filter(site=self.site_b).update(medicines_enabled=True)
		self.assertEqual(self.delete(self.cat_b, self.only_b, site=self.site_b).status_code, 200)
		self.assertTrue(self.link(self.only_b, self.cat_b).suppressed)

	def test_a_site_with_two_settings_rows_needs_the_switch_in_one_that_covers_the_category(self):
		# A second, private row with the switch on whose scope lacks the category's subject.
		CustomSetting.objects.create(
			site=self.site_b, title="Second row", api_public=False, medicines_enabled=True, mcp_enabled=True
		)
		row = CustomSetting.objects.filter(site=self.site_b, title="Second row").get()
		row.scope_subjects.set([self.subject_a])
		self.assertEqual(self.put(self.cat_b, self.only_b, site=self.site_b).status_code, 404)


class EditorReadTest(MedicineEditorFixture):
	def test_the_editor_lists_and_reads_medicines_of_its_site(self):
		listing = self.get("/editor/medicines/")
		self.assertEqual(listing.status_code, 200)
		ids = {row["id"] for row in listing.data["results"]}
		self.assertEqual(ids, {self.auto.pk, self.hand.pk})
		detail = self.get(f"/editor/medicines/{self.auto.pk}/")
		self.assertEqual(detail.status_code, 200)
		self.assertEqual(detail.data["source"], "European Medicines Agency")
		self.assertEqual([c["id"] for c in detail.data["categories"]], [self.cat_a.pk])

	def test_a_hand_link_shows_on_the_new_category(self):
		self.put(self.cat_a2, self.auto)
		detail = self.get(f"/editor/medicines/{self.auto.pk}/")
		self.assertEqual({c["id"] for c in detail.data["categories"]}, {self.cat_a.pk, self.cat_a2.pk})

	def test_medicines_of_other_sites_are_not_listed(self):
		ids = {row["id"] for row in self.get("/editor/medicines/").data["results"]}
		self.assertNotIn(self.only_other.pk, ids)
		self.assertNotIn(self.only_b.pk, ids)

	def test_the_route_needs_an_editor(self):
		self.assertEqual(self.client.get("/editor/medicines/").status_code, 401)


class ThrottleTest(MedicineEditorFixture):
	@override_settings(MCP_EDITOR_RATE_LIMITS={"hour": 1, "day": 100})
	def test_medicine_writes_past_the_hourly_limit_are_429(self):
		self.assertEqual(self.delete(self.cat_a, self.auto).status_code, 200)
		self.assertEqual(self.put(self.cat_a, self.auto).status_code, 429)

	@override_settings(MCP_EDITOR_RATE_LIMITS={"hour": 1, "day": 100})
	def test_reads_are_never_throttled(self):
		self.delete(self.cat_a, self.auto)
		self.assertEqual(self.get("/editor/medicines/").status_code, 200)


class FdaMedicineTest(MedicineEditorFixture):
	"""An FDA medicine goes through the editor link route like an EMA one."""

	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		cls.fda = Medicine.objects.create(
			regulator=Regulator.FDA,
			ema_product_number=None,
			fda_application_number="NDA022561",
			fda_application_type="NDA",
			name="MAVENCLAD",
			substances=["cladribine"],
			status="Approved",
			raw={},
		)
		cls.fda_link = cls.make_link(cls.fda, cls.cat_a, SOURCE.AUTOMATIC)

	def test_removing_an_automatic_fda_link_suppresses_it(self):
		response = self.delete(self.cat_a, self.fda)
		self.assertEqual(response.status_code, 200)
		self.assertTrue(response.data["suppressed"])
		self.assertTrue(self.link(self.fda, self.cat_a).suppressed)

	def test_restoring_it_makes_it_live_again(self):
		self.delete(self.cat_a, self.fda)
		response = self.put(self.cat_a, self.fda)
		self.assertEqual(response.status_code, 200)
		self.assertTrue(response.data["linked"])
		self.assertFalse(self.link(self.fda, self.cat_a).suppressed)

	def test_an_fda_medicine_can_be_added_by_hand_to_another_category(self):
		response = self.put(self.cat_a2, self.fda)
		self.assertEqual(response.status_code, 201)
		self.assertEqual(self.link(self.fda, self.cat_a2).source, SOURCE.MANUAL)

	def test_an_fda_medicine_with_no_link_in_scope_is_still_not_found(self):
		unlinked = Medicine.objects.create(
			regulator=Regulator.FDA,
			ema_product_number=None,
			fda_application_number="NDA999999",
			name="NOLINK",
			status="Approved",
			raw={},
		)
		self.assertEqual(self.put(self.cat_a, unlinked).status_code, 404)

	def test_the_editor_read_route_shows_the_fda_row_with_its_source(self):
		detail = self.get(f"/editor/medicines/{self.fda.pk}/")
		self.assertEqual(detail.status_code, 200)
		self.assertEqual(detail.data["source"], "U.S. Food and Drug Administration")
		self.assertEqual(detail.data["regulator"], "fda")
