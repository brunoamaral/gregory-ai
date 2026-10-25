"""Editing medicine-category links in the admin.

- On a category's page, removing an automatic link suppresses it (so match_medicines never
  brings it back) and the history row names the editor. Manual links delete normally.
- An editor on another team of the same organisation sees the inline read-only.
- The links changelist suppresses and restores by saving each row, so history records the
  editor, and only on the editor's own teams' categories.
- A medicine's page lists its category links scoped to the user's teams.
"""

from io import StringIO
from pathlib import Path

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse
from organizations.models import Organization

from gregory.admin import MedicineCategoryAssignmentInline
from gregory.models import (
	CategoryAssignmentSource,
	CategoryModality,
	Medicine,
	MedicineCategoryAssignment,
	Subject,
	Team,
	TeamCategory,
)
from gregory.tests.test_admin_subject_sources_inline import form_data

User = get_user_model()
BASE = Path(__file__).resolve().parent / "fixtures" / "ema" / "medicines-base.json"
Source = CategoryAssignmentSource


class MedicineLinkAdminTestCase(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.org = Organization.objects.create(name="Links Org", slug="links-org")
		cls.other_org = Organization.objects.create(name="Other Links Org", slug="other-links-org")
		cls.team_a = Team.objects.create(organization=cls.org, name="Team A", slug="links-team-a")
		cls.team_b = Team.objects.create(organization=cls.org, name="Team B", slug="links-team-b")
		cls.team_c = Team.objects.create(organization=cls.other_org, name="Team C", slug="links-team-c")
		subject = Subject.objects.create(subject_name="MS", subject_slug="ms", team=cls.team_a)
		call_command("sync_ema_medicines", file=str(BASE), stdout=StringIO())
		cls.ocrevus = Medicine.objects.get(name="Ocrevus")
		cls.tysabri = Medicine.objects.get(name="Tysabri")
		cls.viagra = Medicine.objects.get(name="Viagra")

		cls.category = TeamCategory.objects.create(
			team=cls.team_a, category_name="Ocrelizumab", category_terms=["ocrelizumab"],
			modality=CategoryModality.BIOLOGIC_ANTIBODY,
		)
		cls.category.subjects.add(subject)
		cls.team_b_category = TeamCategory.objects.create(
			team=cls.team_b, category_name="Natalizumab", category_terms=["natalizumab"],
			modality=CategoryModality.BIOLOGIC_ANTIBODY,
		)
		cls.team_b_category.subjects.add(subject)
		cls.foreign_category = TeamCategory.objects.create(
			team=cls.team_c, category_name="Foreign", category_terms=["x"]
		)
		cls.foreign_category.subjects.add(subject)

		cls.automatic = MedicineCategoryAssignment.objects.create(
			medicine=cls.ocrevus, teamcategory=cls.category, source=Source.AUTOMATIC,
			matched_on="ocrelizumab · Multiple Sclerosis",
		)
		cls.manual = MedicineCategoryAssignment.objects.create(
			medicine=cls.viagra, teamcategory=cls.category, source=Source.MANUAL
		)
		cls.b_link = MedicineCategoryAssignment.objects.create(
			medicine=cls.tysabri, teamcategory=cls.team_b_category, source=Source.AUTOMATIC
		)
		cls.foreign_link = MedicineCategoryAssignment.objects.create(
			medicine=cls.ocrevus, teamcategory=cls.foreign_category, source=Source.AUTOMATIC
		)

		cls.editor_a = cls.staff("editor-a", cls.org, cls.team_a, first_name="Ana", last_name="Editor")
		cls.editor_b = cls.staff("editor-b", cls.org, cls.team_b)
		cls.superuser = User.objects.create_superuser("links-root", "root@example.com", "pw")

	@classmethod
	def staff(cls, username, org, team, **kwargs):
		user = User.objects.create_user(
			username, f"{username}@example.com", "pw", is_staff=True, **kwargs
		)
		org.add_user(user)
		team.members.add(user)
		user.user_permissions.add(
			*Permission.objects.filter(
				content_type__app_label="gregory",
				codename__in=[
					"view_teamcategory", "change_teamcategory",
					"view_medicine",
					"view_medicinecategoryassignment", "add_medicinecategoryassignment",
					"change_medicinecategoryassignment", "delete_medicinecategoryassignment",
				],
			)
		)
		return user

	def change_url(self, category=None):
		return reverse("admin:gregory_teamcategory_change", args=[(category or self.category).pk])

	def inline_post(self, user, category=None, edit=None):
		"""Resubmit the category page as ``user`` does, with ``edit(data, prefix, index_of)``
		applied to the form data, and return the response."""
		self.client.force_login(user)
		url = self.change_url(category)
		response = self.client.get(url)
		self.assertEqual(response.status_code, 200)
		data = form_data(response)
		formset = next(
			f.formset for f in response.context["inline_admin_formsets"]
			if f.formset.model is MedicineCategoryAssignment
		)
		index_of = {form.instance.medicine_id: i for i, form in enumerate(formset.forms) if form.instance.pk}
		if edit:
			edit(data, formset.prefix, index_of)
		return self.client.post(url, data)


class InlineTests(MedicineLinkAdminTestCase):
	def test_removing_an_automatic_link_suppresses_it(self):
		response = self.inline_post(
			self.editor_a,
			edit=lambda data, prefix, index_of: data.update(
				{f"{prefix}-{index_of[self.ocrevus.pk]}-DELETE": "on"}
			),
		)
		self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["adminform"].form.errors)
		self.automatic.refresh_from_db()
		self.assertTrue(self.automatic.suppressed)
		self.assertEqual(self.automatic.source, Source.AUTOMATIC)

	def test_the_suppression_is_in_history_under_the_editor(self):
		self.inline_post(
			self.editor_a,
			edit=lambda data, prefix, index_of: data.update(
				{f"{prefix}-{index_of[self.ocrevus.pk]}-DELETE": "on"}
			),
		)
		row = self.automatic.history.latest("history_date", "history_id")
		self.assertEqual(row.history_type, "~")
		self.assertTrue(row.suppressed)
		self.assertEqual(row.editor_user, self.editor_a)
		self.assertEqual(row.editor_label, "Ana Editor <editor-a@example.com>")
		self.assertEqual(row.via, "admin")

	def test_the_matcher_does_not_bring_a_suppressed_link_back(self):
		self.inline_post(
			self.editor_a,
			edit=lambda data, prefix, index_of: data.update(
				{f"{prefix}-{index_of[self.ocrevus.pk]}-DELETE": "on"}
			),
		)
		call_command("match_medicines", stdout=StringIO())
		link = MedicineCategoryAssignment.objects.get(medicine=self.ocrevus, teamcategory=self.category)
		self.assertTrue(link.suppressed)

	def test_a_manual_link_deletes_normally(self):
		self.inline_post(
			self.editor_a,
			edit=lambda data, prefix, index_of: data.update(
				{f"{prefix}-{index_of[self.viagra.pk]}-DELETE": "on"}
			),
		)
		self.assertFalse(MedicineCategoryAssignment.objects.filter(pk=self.manual.pk).exists())
		self.assertTrue(MedicineCategoryAssignment.objects.filter(pk=self.automatic.pk).exists())

	def test_ticking_suppressed_hides_a_link_and_unticking_restores_it(self):
		self.inline_post(
			self.editor_a,
			edit=lambda data, prefix, index_of: data.update(
				{f"{prefix}-{index_of[self.ocrevus.pk]}-suppressed": "on"}
			),
		)
		self.automatic.refresh_from_db()
		self.assertTrue(self.automatic.suppressed)
		self.assertEqual(self.automatic.history.latest("history_date", "history_id").editor_user, self.editor_a)

		def untick(data, prefix, index_of):
			data.pop(f"{prefix}-{index_of[self.ocrevus.pk]}-suppressed", None)

		self.inline_post(self.editor_a, edit=untick)
		self.automatic.refresh_from_db()
		self.assertFalse(self.automatic.suppressed)

	def test_an_editor_adds_a_manual_link(self):
		def add(data, prefix, index_of):
			total = int(data[f"{prefix}-TOTAL_FORMS"])
			data[f"{prefix}-TOTAL_FORMS"] = str(total + 1)
			data[f"{prefix}-{total}-medicine"] = str(self.tysabri.pk)

		response = self.inline_post(self.editor_a, edit=add)
		self.assertEqual(response.status_code, 302)
		link = MedicineCategoryAssignment.objects.get(medicine=self.tysabri, teamcategory=self.category)
		self.assertEqual(link.source, Source.MANUAL)
		self.assertEqual(link.matched_on, "")
		created = link.history.get(history_type="+")
		self.assertEqual(created.editor_user, self.editor_a)

	def test_a_saved_link_keeps_its_medicine(self):
		self.inline_post(
			self.editor_a,
			edit=lambda data, prefix, index_of: data.update(
				{f"{prefix}-{index_of[self.ocrevus.pk]}-medicine": str(self.tysabri.pk)}
			),
		)
		self.automatic.refresh_from_db()
		self.assertEqual(self.automatic.medicine, self.ocrevus)

	def test_an_editor_of_another_team_sees_the_inline_read_only(self):
		self.client.force_login(self.editor_b)
		response = self.client.get(self.change_url())
		self.assertEqual(response.status_code, 200)
		inline = next(
			f for f in response.context["inline_admin_formsets"]
			if f.formset.model is MedicineCategoryAssignment
		)
		self.assertFalse(inline.has_change_permission)
		self.assertFalse(inline.has_add_permission)
		self.assertFalse(inline.has_delete_permission)

	def test_an_editor_of_another_team_cannot_post_a_change(self):
		self.client.force_login(self.editor_b)
		url = self.change_url()
		response = self.client.get(url)
		data = form_data(response)
		prefix = next(
			f.formset.prefix for f in response.context["inline_admin_formsets"]
			if f.formset.model is MedicineCategoryAssignment
		)
		data[f"{prefix}-0-DELETE"] = "on"
		self.assertEqual(self.client.post(url, data).status_code, 403)
		self.automatic.refresh_from_db()
		self.assertFalse(self.automatic.suppressed)

	def test_the_inline_checks_the_parent_through_team_scope(self):
		request = RequestFactory().get("/")
		inline = MedicineCategoryAssignmentInline(TeamCategory, admin.site)
		request.user = self.editor_a
		self.assertTrue(inline.has_change_permission(request, self.category))
		self.assertFalse(inline.has_change_permission(request, self.team_b_category))
		request.user = self.superuser
		self.assertTrue(inline.has_change_permission(request, self.team_b_category))

	def test_the_category_list_counts_live_medicines(self):
		self.client.force_login(self.superuser)
		self.automatic.suppressed = True
		self.automatic.save()
		response = self.client.get(reverse("admin:gregory_teamcategory_changelist"))
		self.assertEqual(response.status_code, 200)
		counts = {c.category_name: c.medicine_total for c in response.context["cl"].result_list}
		# Ocrevus is suppressed; Viagra (manual) still counts.
		self.assertEqual(counts["Ocrelizumab"], 1)
		self.assertEqual(counts["Natalizumab"], 1)


class ChangelistTests(MedicineLinkAdminTestCase):
	changelist = "admin:gregory_medicinecategoryassignment_changelist"

	def run_action(self, user, action, *links):
		self.client.force_login(user)
		return self.client.post(
			reverse(self.changelist),
			{"action": action, "_selected_action": [str(link.pk) for link in links]},
			follow=True,
		)

	def test_suppress_saves_each_row_so_history_names_the_editor(self):
		before = self.automatic.history.count()
		self.run_action(self.editor_a, "suppress_links", self.automatic)
		self.automatic.refresh_from_db()
		self.assertTrue(self.automatic.suppressed)
		self.assertEqual(self.automatic.history.count(), before + 1)
		row = self.automatic.history.latest("history_date", "history_id")
		self.assertEqual(row.editor_user, self.editor_a)
		self.assertEqual(row.via, "admin")

	def test_restore_clears_suppressed_and_records_the_editor(self):
		self.automatic.suppressed = True
		self.automatic.save()
		self.run_action(self.editor_a, "restore_links", self.automatic)
		self.automatic.refresh_from_db()
		self.assertFalse(self.automatic.suppressed)
		row = self.automatic.history.latest("history_date", "history_id")
		self.assertEqual(row.editor_user, self.editor_a)
		self.assertFalse(row.suppressed)

	def test_an_action_leaves_another_teams_links_alone(self):
		response = self.run_action(self.editor_a, "suppress_links", self.automatic, self.b_link)
		self.automatic.refresh_from_db()
		self.b_link.refresh_from_db()
		self.assertTrue(self.automatic.suppressed)
		self.assertFalse(self.b_link.suppressed)
		self.assertContains(response, "belong to another team")

	def test_staff_see_their_organisations_links_but_not_another_organisations(self):
		self.client.force_login(self.editor_a)
		response = self.client.get(reverse(self.changelist))
		shown = {link.pk for link in response.context["cl"].result_list}
		self.assertEqual(shown, {self.automatic.pk, self.manual.pk, self.b_link.pk})

	def test_superuser_sees_every_link(self):
		self.client.force_login(self.superuser)
		response = self.client.get(reverse(self.changelist))
		self.assertEqual(response.context["cl"].result_count, 4)

	def test_nobody_adds_or_deletes_from_this_page(self):
		self.client.force_login(self.superuser)
		self.assertEqual(self.client.get(reverse("admin:gregory_medicinecategoryassignment_add")).status_code, 403)
		url = reverse("admin:gregory_medicinecategoryassignment_delete", args=[self.automatic.pk])
		self.assertEqual(self.client.get(url).status_code, 403)

	def test_the_change_page_edits_only_suppressed(self):
		self.client.force_login(self.editor_a)
		url = reverse("admin:gregory_medicinecategoryassignment_change", args=[self.automatic.pk])
		response = self.client.post(
			url, {"suppressed": "on", "medicine": self.tysabri.pk, "matched_on": "forged"}
		)
		self.assertEqual(response.status_code, 302)
		self.automatic.refresh_from_db()
		self.assertTrue(self.automatic.suppressed)
		self.assertEqual(self.automatic.medicine, self.ocrevus)
		self.assertEqual(self.automatic.matched_on, "ocrelizumab · Multiple Sclerosis")

	def test_the_change_page_of_another_teams_link_is_read_only(self):
		self.client.force_login(self.editor_a)
		url = reverse("admin:gregory_medicinecategoryassignment_change", args=[self.b_link.pk])
		self.assertEqual(self.client.post(url, {"suppressed": "on"}).status_code, 403)
		self.b_link.refresh_from_db()
		self.assertFalse(self.b_link.suppressed)


class MedicineAdminLinksTests(MedicineLinkAdminTestCase):
	def test_a_medicines_page_lists_only_the_users_teams_links(self):
		self.client.force_login(self.editor_a)
		response = self.client.get(reverse("admin:gregory_medicine_change", args=[self.ocrevus.pk]))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Ocrelizumab")
		self.assertNotContains(response, "Foreign")

	def test_a_superuser_sees_every_link_on_a_medicines_page(self):
		self.client.force_login(self.superuser)
		response = self.client.get(reverse("admin:gregory_medicine_change", args=[self.ocrevus.pk]))
		self.assertContains(response, "Ocrelizumab")
		self.assertContains(response, "Foreign")

	def test_the_linked_filter(self):
		self.client.force_login(self.editor_a)
		url = reverse("admin:gregory_medicine_changelist")
		linked = {m.name for m in self.client.get(url, {"has_link": "yes"}).context["cl"].result_list}
		# Team A's own categories link Ocrevus and Viagra; Tysabri is Team B's.
		self.assertEqual(linked, {"Ocrevus", "Viagra"})
		unlinked = {m.name for m in self.client.get(url, {"has_link": "no"}).context["cl"].result_list}
		self.assertNotIn("Ocrevus", unlinked)
		self.assertIn("Tysabri", unlinked)

	def test_a_suppressed_link_does_not_count_as_linked(self):
		self.automatic.suppressed = True
		self.automatic.save()
		self.client.force_login(self.editor_a)
		url = reverse("admin:gregory_medicine_changelist")
		linked = {m.name for m in self.client.get(url, {"has_link": "yes"}).context["cl"].result_list}
		self.assertEqual(linked, {"Viagra"})

	def test_subject_form_exposes_the_ema_areas(self):
		self.client.force_login(self.superuser)
		subject = Subject.objects.get(subject_slug="ms")
		response = self.client.get(reverse("admin:gregory_subject_change", args=[subject.pk]))
		self.assertContains(response, "ema_therapeutic_areas")

	def test_category_form_exposes_the_medicine_fields(self):
		self.client.force_login(self.superuser)
		response = self.client.get(self.change_url())
		self.assertContains(response, "medicine_terms")
		self.assertContains(response, "medicine_include_combinations")
