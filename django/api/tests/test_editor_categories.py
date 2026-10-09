"""
The category routes on ``/editor/`` (api/editor_views.py): create and change a
category, and assign an article to one by hand. Same fixture as
test_editor_endpoints: ana edits sites A and B, ben reads site A only.
"""

from django.core.management import call_command

from api.tests.test_editor_endpoints import EditorFixture, _subject
from gregory.models import (
	ArticleCategoryAssignment,
	CategoryAssignmentSource,
	CategoryType,
	Subject,
	TeamCategory,
)
from sitesettings.models import CustomSetting


def _add_to_scope(subject, site):
	CustomSetting.objects.get(site=site).scope_subjects.add(subject)


class CategoryFixture(EditorFixture):
	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		# A second subject in subject A's team, so a category can span two.
		cls.subject_a2 = Subject.objects.create(
			team=cls.subject_a.team, subject_name="Subject A2", subject_slug="subject-a2"
		)
		_add_to_scope(cls.subject_a2, cls.site_a)
		cls.cat_a = TeamCategory.objects.create(
			team=cls.subject_a.team, category_name="Cordycepin", category_terms=["cordycepin", "3'-deoxyadenosine"]
		)
		cls.cat_a.subjects.add(cls.subject_a)
		cls.cat_shared = TeamCategory.objects.create(
			team=cls.subject_a.team, category_name="Shared", category_terms=["shared"]
		)
		cls.cat_shared.subjects.add(cls.subject_a, cls.subject_b)
		cls.cat_other = TeamCategory.objects.create(
			team=cls.subject_other.team, category_name="Other", category_terms=["other"]
		)
		cls.cat_other.subjects.add(cls.subject_other)

	def create(self, **body):
		payload = {"category_name": "Apotransferrin", "subject_ids": [self.subject_a.pk], "category_terms": ["apotransferrin"]}
		payload.update(body)
		return self.send("post", "/editor/categories/", payload)


class CreateCategoryTest(CategoryFixture):
	def test_creates_an_automatic_category_in_the_subjects_team(self):
		response = self.create(
			category_terms=["apotransferrin", "apo-transferrin"],
			category_description="The iron-free form of transferrin.",
			modality="biologic_antibody",
		)
		self.assertEqual(response.status_code, 201, response.content)
		body = response.json()
		category = TeamCategory.objects.get(pk=body["id"])
		self.assertEqual(category.team, self.subject_a.team)
		self.assertEqual(list(category.subjects.all()), [self.subject_a])
		self.assertEqual(category.category_slug, "apotransferrin")
		self.assertEqual(category.category_type, CategoryType.AUTOMATIC)
		self.assertEqual(body["category_terms"], ["apotransferrin", "apo-transferrin"])
		self.assertEqual(body["subject_ids"], [self.subject_a.pk])
		self.assertEqual(body["modality"], "biologic_antibody")
		self.assertEqual(body["article_count"], 0)
		self.assertIsNone(body["last_synced_at"])
		self.assertIn("ana", body["updated_by"])

	def test_history_names_the_editor_and_the_mcp_door(self):
		category_id = self.create().json()["id"]
		row = TeamCategory.history.get(id=category_id)
		self.assertEqual(row.history_type, "+")
		self.assertEqual(row.editor_user, self.ana)
		self.assertEqual(row.via, "mcp")

	def test_terms_are_trimmed_and_deduplicated_ignoring_case(self):
		body = self.create(category_terms=[" apotransferrin ", "", "ApoTransferrin", "apo-transferrin"]).json()
		self.assertEqual(body["category_terms"], ["apotransferrin", "apo-transferrin"])

	def test_an_automatic_category_needs_a_term(self):
		response = self.create(category_terms=["", "  "])
		self.assertEqual(response.status_code, 400)
		self.assertIn("category_terms", response.json())
		self.assertFalse(TeamCategory.objects.filter(category_name="Apotransferrin").exists())

	def test_a_manual_category_may_have_no_terms(self):
		response = self.create(category_terms=[], category_type="manual")
		self.assertEqual(response.status_code, 201, response.content)

	def test_subjects_must_be_in_the_sites_scope(self):
		for subject in (self.subject_b, self.subject_other):
			with self.subTest(subject=subject.subject_name):
				response = self.create(subject_ids=[subject.pk])
				self.assertEqual(response.status_code, 404)

	def test_unknown_subject_is_404(self):
		self.assertEqual(self.create(subject_ids=[999999]).status_code, 404)

	def test_subjects_from_two_teams_are_refused(self):
		response = self.send(
			"post",
			"/editor/categories/",
			{"category_name": "Mixed", "subject_ids": [self.subject_a.pk, self.subject_b.pk], "category_terms": ["x"]},
			site=self.site_b,
		)
		# subject_a is outside site B's scope, so it is not found before the team check.
		self.assertEqual(response.status_code, 404)

	def test_two_subjects_from_one_team(self):
		body = self.create(subject_ids=[self.subject_a.pk, self.subject_a2.pk]).json()
		self.assertEqual(body["subject_ids"], sorted([self.subject_a.pk, self.subject_a2.pk]))

	def test_a_taken_slug_is_409_naming_a_visible_category(self):
		response = self.create(category_name="Cordycepin")
		self.assertEqual(response.status_code, 409)
		self.assertEqual(response.json()["category_id"], self.cat_a.pk)

	def test_a_taken_slug_outside_the_scope_is_409_without_its_id(self):
		response = self.create(category_name="Other")
		self.assertEqual(response.status_code, 409)
		self.assertNotIn("category_id", response.json())

	def test_explicit_slug(self):
		body = self.create(category_slug="apo-tf").json()
		self.assertEqual(body["category_slug"], "apo-tf")

	def test_invalid_modality_is_400(self):
		self.assertEqual(self.create(modality="potion").status_code, 400)

	def test_read_only_editor_cannot_create(self):
		response = self.send(
			"post",
			"/editor/categories/",
			{"category_name": "X", "subject_ids": [self.subject_a.pk], "category_terms": ["x"]},
			user=self.ben,
		)
		self.assertEqual(response.status_code, 403)

	def test_list_and_detail_still_read_the_sites_categories(self):
		names = {c["category_name"] for c in self.get("/editor/categories/").json()["results"]}
		self.assertIn("Cordycepin", names)
		self.assertNotIn("Other", names)
		self.assertEqual(self.get(f"/editor/categories/{self.cat_a.pk}/").status_code, 200)

	def test_the_new_category_is_matched_by_the_next_rebuild(self):
		self.art_a.title = "Apotransferrin promotes remyelination"
		self.art_a.save()
		category_id = self.create().json()["id"]
		call_command("rebuild_categories", category=category_id, verbosity=0)
		assignment = ArticleCategoryAssignment.objects.get(teamcategory_id=category_id, articles=self.art_a)
		self.assertEqual(assignment.source, CategoryAssignmentSource.AUTOMATIC)
		# The sync is bookkeeping: no history row for the category, none for the match.
		self.assertEqual(TeamCategory.history.filter(id=category_id).count(), 1)
		self.assertFalse(ArticleCategoryAssignment.history.filter(teamcategory_id=category_id).exists())


class UpdateCategoryTest(CategoryFixture):
	def patch(self, category, body, **kwargs):
		return self.send("patch", f"/editor/categories/{category.pk}/", body, **kwargs)

	def test_remove_and_add_terms_ignoring_case(self):
		response = self.patch(self.cat_a, {"remove_terms": ["3'-DEOXYADENOSINE"], "add_terms": ["Cordycepin", "cordyceps militaris"]})
		self.assertEqual(response.status_code, 200, response.content)
		self.assertEqual(response.json()["category_terms"], ["cordycepin", "cordyceps militaris"])

	def test_replace_terms(self):
		body = self.patch(self.cat_a, {"category_terms": ["cordycepin"]}).json()
		self.assertEqual(body["category_terms"], ["cordycepin"])

	def test_replace_and_edit_terms_together_is_400(self):
		response = self.patch(self.cat_a, {"category_terms": ["a"], "add_terms": ["b"]})
		self.assertEqual(response.status_code, 400)

	def test_removing_every_term_of_an_automatic_category_is_400(self):
		response = self.patch(self.cat_a, {"remove_terms": ["cordycepin", "3'-deoxyadenosine"]})
		self.assertEqual(response.status_code, 400)
		self.cat_a.refresh_from_db()
		self.assertEqual(len(self.cat_a.category_terms), 2)

	def test_name_description_modality_and_subjects(self):
		body = self.patch(
			self.cat_a,
			{
				"category_name": "Cordycepin (3'-deoxyadenosine)",
				"category_description": "An adenosine analogue.",
				"modality": "natural_product",
				"subject_ids": [self.subject_a.pk, self.subject_a2.pk],
			},
		).json()
		self.assertEqual(body["category_name"], "Cordycepin (3'-deoxyadenosine)")
		self.assertEqual(body["category_slug"], "cordycepin")
		self.assertEqual(body["modality"], "natural_product")
		self.assertEqual(body["subject_ids"], sorted([self.subject_a.pk, self.subject_a2.pk]))

	def test_empty_description_clears_it(self):
		self.cat_a.category_description = "Old"
		self.cat_a.save()
		self.assertIsNone(self.patch(self.cat_a, {"category_description": ""}).json()["category_description"])

	def test_history_records_the_change_and_who_made_it(self):
		self.patch(self.cat_a, {"add_terms": ["cordyceps militaris"]})
		row = TeamCategory.history.filter(id=self.cat_a.pk).order_by("-history_date").first()
		self.assertEqual(row.history_type, "~")
		self.assertEqual(row.via, "mcp")
		self.assertIn("cordyceps militaris", row.category_terms)

	def test_unchanged_values_write_no_history(self):
		before = TeamCategory.history.filter(id=self.cat_a.pk).count()
		self.patch(self.cat_a, {"add_terms": ["cordycepin"]})
		self.assertEqual(TeamCategory.history.filter(id=self.cat_a.pk).count(), before)

	def test_empty_body_is_400(self):
		self.assertEqual(self.patch(self.cat_a, {}).status_code, 400)

	def test_a_category_shared_with_another_site_is_403(self):
		response = self.patch(self.cat_shared, {"add_terms": ["x"]})
		self.assertEqual(response.status_code, 403)
		self.assertIn("admin", response.json()["detail"])

	def test_a_category_outside_the_scope_is_404(self):
		self.assertEqual(self.patch(self.cat_other, {"add_terms": ["x"]}).status_code, 404)

	def test_subjects_outside_the_scope_or_team_are_refused(self):
		self.assertEqual(self.patch(self.cat_a, {"subject_ids": [self.subject_b.pk]}).status_code, 404)
		other_team_subject = _subject(self.org, "Subject A other team")
		_add_to_scope(other_team_subject, self.site_a)
		self.assertEqual(self.patch(self.cat_a, {"subject_ids": [other_team_subject.pk]}).status_code, 400)

	def test_put_is_not_allowed(self):
		response = self.send("put", f"/editor/categories/{self.cat_a.pk}/", {"category_name": "x"})
		self.assertEqual(response.status_code, 405)


class ArticleCategoryTest(CategoryFixture):
	def path(self, article, category):
		return f"/editor/articles/{article.pk}/categories/{category.pk}/"

	def test_assign_creates_a_manual_assignment(self):
		response = self.send("put", self.path(self.art_a, self.cat_a))
		self.assertEqual(response.status_code, 201, response.content)
		self.assertEqual(response.json()["source"], "manual")
		row = ArticleCategoryAssignment.history.get(articles_id=self.art_a.pk, teamcategory_id=self.cat_a.pk)
		self.assertEqual((row.editor_user, row.via), (self.ana, "mcp"))

	def test_assigning_twice_is_200_and_changes_nothing(self):
		self.send("put", self.path(self.art_a, self.cat_a))
		response = self.send("put", self.path(self.art_a, self.cat_a))
		self.assertEqual(response.status_code, 200)
		self.assertEqual(ArticleCategoryAssignment.history.filter(articles_id=self.art_a.pk).count(), 1)

	def test_assigning_a_pipeline_match_pins_it(self):
		self.art_a.team_categories.add(self.cat_a, through_defaults={"source": CategoryAssignmentSource.AUTOMATIC})
		response = self.send("put", self.path(self.art_a, self.cat_a))
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()["source"], "manual")
		call_command("rebuild_categories", category=self.cat_a.pk, verbosity=0)
		self.assertTrue(self.art_a.team_categories.filter(pk=self.cat_a.pk).exists())

	def test_a_shared_category_can_be_assigned(self):
		self.assertEqual(self.send("put", self.path(self.art_a, self.cat_shared)).status_code, 201)

	def test_article_or_category_outside_the_scope_is_404(self):
		self.assertEqual(self.send("put", self.path(self.art_b, self.cat_a)).status_code, 404)
		self.assertEqual(self.send("put", self.path(self.art_a, self.cat_other)).status_code, 404)

	def test_unassign_deletes_a_manual_assignment(self):
		self.send("put", self.path(self.art_a, self.cat_a))
		response = self.send("delete", self.path(self.art_a, self.cat_a))
		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()["removed"], "manual")
		self.assertFalse(self.art_a.team_categories.filter(pk=self.cat_a.pk).exists())

	def test_unassigning_a_pipeline_match_is_409(self):
		self.art_a.team_categories.add(self.cat_a, through_defaults={"source": CategoryAssignmentSource.AUTOMATIC})
		response = self.send("delete", self.path(self.art_a, self.cat_a))
		self.assertEqual(response.status_code, 409)
		self.assertTrue(self.art_a.team_categories.filter(pk=self.cat_a.pk).exists())

	def test_unassigning_an_article_not_in_the_category_is_404(self):
		self.assertEqual(self.send("delete", self.path(self.art_a, self.cat_a)).status_code, 404)

	def test_read_only_editor_cannot_assign(self):
		self.assertEqual(self.send("put", self.path(self.art_a, self.cat_a), user=self.ben).status_code, 403)

	def test_history_lists_hand_assignments_in_scope(self):
		self.send("put", self.path(self.art_a, self.cat_a))
		self.send("delete", self.path(self.art_a, self.cat_a))
		entries = self.get(f"/editor/articles/{self.art_a.pk}/history/").json()["entries"]
		category_entries = [e for e in entries if e["kind"] == "category"]
		self.assertEqual([e["change"] for e in category_entries], ["deleted", "created"])
		self.assertEqual(category_entries[0]["details"], {"category_id": self.cat_a.pk, "source": "manual"})
		self.assertEqual(category_entries[0]["via"], "mcp")
