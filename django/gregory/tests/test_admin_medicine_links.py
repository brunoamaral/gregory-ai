"""Editing medicine-category links in the admin.

- On a category's page, removing an automatic link suppresses it (so match_medicines never
  brings it back) and the history row names the editor. Manual links delete normally.
- An editor on another team of the same organisation sees the inline read-only.
- The links changelist suppresses and restores by saving each row, so history records the
  editor, and only on the editor's own teams' categories.
- A medicine's page lists its category and subject links scoped to the user's teams.
- The Tracked and Condition filters, the Subject page's medicine inline (only `suppressed`
  is editable, no add, no delete) and linking a category's medicines when it is saved.
"""

from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse
from organizations.models import Organization

from gregory.admin import MedicineCategoryAssignmentInline, MedicineSubjectLinkInline
from gregory.models import (
	CategoryAssignmentSource,
	CategoryModality,
	Medicine,
	MedicineCategoryAssignment,
	MedicineSubjectLink,
	Subject,
	Team,
	TeamCategory,
)
from gregory.roles import EDITOR_GROUP, sync_editor_group
from gregory.services.medicine_linking import link_category, link_subject
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

	def test_the_tracked_filter(self):
		self.client.force_login(self.editor_a)
		url = reverse("admin:gregory_medicine_changelist")
		linked = {m.name for m in self.client.get(url, {"tracked": "yes"}).context["cl"].result_list}
		# Team A's own categories link Ocrevus and Viagra; Tysabri is Team B's.
		self.assertEqual(linked, {"Ocrevus", "Viagra"})
		unlinked = {m.name for m in self.client.get(url, {"tracked": "no"}).context["cl"].result_list}
		self.assertNotIn("Ocrevus", unlinked)
		self.assertIn("Tysabri", unlinked)

	def test_a_suppressed_link_does_not_count_as_tracked(self):
		self.automatic.suppressed = True
		self.automatic.save()
		self.client.force_login(self.editor_a)
		url = reverse("admin:gregory_medicine_changelist")
		linked = {m.name for m in self.client.get(url, {"tracked": "yes"}).context["cl"].result_list}
		self.assertEqual(linked, {"Viagra"})

	def test_subject_form_exposes_the_ema_areas(self):
		self.client.force_login(self.superuser)
		subject = Subject.objects.get(subject_slug="ms")
		response = self.client.get(reverse("admin:gregory_subject_change", args=[subject.pk]))
		self.assertContains(response, "ema_therapeutic_areas")

	def test_subject_form_exposes_the_fda_terms_after_the_ema_areas(self):
		self.client.force_login(self.superuser)
		subject = Subject.objects.get(subject_slug="ms")
		response = self.client.get(reverse("admin:gregory_subject_change", args=[subject.pk]))
		content = response.content.decode()
		self.assertIn("FDA indication terms", content)
		self.assertLess(content.index("ema_therapeutic_areas"), content.index("fda_indication_terms"))

	def test_saving_the_fda_terms_from_the_subject_form(self):
		from gregory.admin import SubjectAdminForm

		subject = Subject.objects.get(subject_slug="ms")
		form = SubjectAdminForm(instance=subject)
		field = form.fields["fda_indication_terms"]
		self.assertEqual(field.label, "FDA indication terms (comma separated)")
		self.assertIn("alzheimer", field.help_text)
		self.assertEqual(field.clean("multiple sclerosis, alzheimer"), ["multiple sclerosis", "alzheimer"])
		self.assertEqual(field.clean(""), [])

	def test_category_form_exposes_the_medicine_fields(self):
		self.client.force_login(self.superuser)
		response = self.client.get(self.change_url())
		self.assertContains(response, "medicine_terms")
		self.assertContains(response, "medicine_include_combinations")


class SubjectLinkAdminTestCase(MedicineLinkAdminTestCase):
	"""Subjects with EMA areas on three teams, their links made by the matcher's own function."""

	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		cls.ms = Subject.objects.get(subject_slug="ms")
		cls.ms.ema_therapeutic_areas = ["Multiple Sclerosis"]
		cls.ms.save()
		cls.team_b_subject = Subject.objects.create(
			subject_name="Team B condition", subject_slug="b-condition", team=cls.team_b,
			ema_therapeutic_areas=["Multiple Sclerosis"],
		)
		cls.foreign_subject = Subject.objects.create(
			subject_name="Foreign condition", subject_slug="f-condition", team=cls.team_c,
			ema_therapeutic_areas=["Multiple Sclerosis"],
		)
		cls.no_areas = Subject.objects.create(subject_name="Research area", subject_slug="research", team=cls.team_a)
		for subject in (cls.ms, cls.team_b_subject, cls.foreign_subject, cls.no_areas):
			link_subject(subject)
		cls.ms_link = MedicineSubjectLink.objects.get(subject=cls.ms, medicine=cls.ocrevus)
		view_change = Permission.objects.filter(
			content_type__app_label="gregory",
			codename__in=[
				"view_subject", "change_subject", "view_medicinesubjectlink", "change_medicinesubjectlink",
			],
		)
		for user in (cls.editor_a, cls.editor_b):
			user.user_permissions.add(*view_change)

	def subject_url(self, subject=None):
		return reverse("admin:gregory_subject_change", args=[(subject or self.ms).pk])

	def condition_choices(self, user):
		self.client.force_login(user)
		response = self.client.get(reverse("admin:gregory_medicine_changelist"))
		spec = next(s for s in response.context["cl"].filter_specs if s.title == "condition")
		return {int(pk) for pk, _ in spec.lookup_choices}

	def listed(self, user, **params):
		self.client.force_login(user)
		response = self.client.get(reverse("admin:gregory_medicine_changelist"), params)
		self.assertEqual(response.status_code, 200)
		return {m.name for m in response.context["cl"].result_list}


class ConditionFilterTests(SubjectLinkAdminTestCase):
	def test_it_offers_only_the_users_teams_subjects_with_areas(self):
		self.assertEqual(self.condition_choices(self.editor_a), {self.ms.pk})
		self.assertEqual(self.condition_choices(self.editor_b), {self.team_b_subject.pk})

	def test_a_superuser_is_offered_every_subject_with_areas(self):
		self.assertEqual(
			self.condition_choices(self.superuser),
			{self.ms.pk, self.team_b_subject.pk, self.foreign_subject.pk},
		)

	def test_it_keeps_the_medicines_approved_for_the_subject(self):
		names = self.listed(self.editor_a, condition=self.ms.pk)
		expected = set(
			MedicineSubjectLink.objects.filter(subject=self.ms).values_list("medicine__name", flat=True)
		)
		self.assertIn("Ocrevus", names)
		self.assertIn("Tysabri", names)
		self.assertNotIn("Viagra", names)
		self.assertEqual(names, expected)

	def test_a_suppressed_subject_link_does_not_count(self):
		self.ms_link.suppressed = True
		self.ms_link.save()
		self.assertNotIn("Ocrevus", self.listed(self.editor_a, condition=self.ms.pk))

	def test_another_teams_subject_is_ignored(self):
		self.assertEqual(self.listed(self.editor_a, condition=self.team_b_subject.pk), set())
		self.assertEqual(self.listed(self.editor_a, condition=self.foreign_subject.pk), set())
		self.assertEqual(self.listed(self.editor_a, condition="not-a-number"), set())

	def test_it_combines_with_the_tracked_filter(self):
		# Team A's category tracks Ocrevus (automatic) and Viagra (manual); Tysabri is Team B's.
		untracked = self.listed(self.editor_a, condition=self.ms.pk, tracked="no")
		tracked = self.listed(self.editor_a, condition=self.ms.pk, tracked="yes")
		self.assertIn("Tysabri", untracked)
		self.assertNotIn("Ocrevus", untracked)
		self.assertEqual(tracked, {"Ocrevus"})


class MedicinePageTests(SubjectLinkAdminTestCase):
	def test_the_page_lists_only_the_users_teams_subject_links(self):
		self.client.force_login(self.editor_a)
		response = self.client.get(reverse("admin:gregory_medicine_change", args=[self.ocrevus.pk]))
		self.assertContains(response, "Subject links")
		self.assertContains(response, self.ms.subject_name)
		self.assertNotContains(response, "Team B condition")
		self.assertNotContains(response, "Foreign condition")

	def test_a_superuser_sees_every_subject_link(self):
		self.client.force_login(self.superuser)
		response = self.client.get(reverse("admin:gregory_medicine_change", args=[self.ocrevus.pk]))
		self.assertContains(response, "Team B condition")
		self.assertContains(response, "Foreign condition")

	def test_the_subject_links_are_read_only(self):
		self.client.force_login(self.superuser)
		response = self.client.get(reverse("admin:gregory_medicine_change", args=[self.ocrevus.pk]))
		inline = next(
			f for f in response.context["inline_admin_formsets"] if f.formset.model is MedicineSubjectLink
		)
		self.assertFalse(inline.has_change_permission)
		self.assertFalse(inline.has_add_permission)
		self.assertFalse(inline.has_delete_permission)

	def test_a_copy_shows_its_lead_and_a_lead_its_copies(self):
		self.tyruko = Medicine.objects.get(name="Tyruko")
		self.tyruko.grouped_under = self.tysabri
		self.tyruko.save()
		self.client.force_login(self.editor_a)
		copy_page = self.client.get(reverse("admin:gregory_medicine_change", args=[self.tyruko.pk]))
		self.assertContains(copy_page, "Grouped under")
		self.assertContains(copy_page, reverse("admin:gregory_medicine_change", args=[self.tysabri.pk]))
		lead_page = self.client.get(reverse("admin:gregory_medicine_change", args=[self.tysabri.pk]))
		self.assertContains(lead_page, "Grouped products")
		self.assertContains(lead_page, reverse("admin:gregory_medicine_change", args=[self.tyruko.pk]))


class SubjectInlineTests(SubjectLinkAdminTestCase):
	def inline_post(self, user, edit=None, subject=None):
		self.client.force_login(user)
		url = self.subject_url(subject)
		response = self.client.get(url)
		self.assertEqual(response.status_code, 200)
		data = form_data(response)
		formset = next(
			f.formset for f in response.context["inline_admin_formsets"]
			if f.formset.model is MedicineSubjectLink
		)
		index_of = {form.instance.medicine_id: i for i, form in enumerate(formset.forms) if form.instance.pk}
		if edit:
			edit(data, formset.prefix, index_of)
		return self.client.post(url, data)

	def inline(self, user, subject=None):
		self.client.force_login(user)
		response = self.client.get(self.subject_url(subject))
		self.assertEqual(response.status_code, 200)
		return next(
			f for f in response.context["inline_admin_formsets"] if f.formset.model is MedicineSubjectLink
		)

	def test_the_inline_lists_the_subjects_medicines_by_name(self):
		inline = self.inline(self.editor_a)
		names = [form.instance.medicine.name for form in inline.formset.forms if form.instance.pk]
		self.assertEqual(names, sorted(names))
		self.assertIn("Ocrevus", names)

	def test_ticking_suppressed_hides_the_medicine_and_unticking_restores_it(self):
		response = self.inline_post(
			self.editor_a,
			edit=lambda data, prefix, index_of: data.update({f"{prefix}-{index_of[self.ocrevus.pk]}-suppressed": "on"}),
		)
		self.assertEqual(response.status_code, 302, getattr(response, "context", None) and response.context["adminform"].form.errors)
		self.ms_link.refresh_from_db()
		self.assertTrue(self.ms_link.suppressed)

		def untick(data, prefix, index_of):
			data.pop(f"{prefix}-{index_of[self.ocrevus.pk]}-suppressed", None)

		self.inline_post(self.editor_a, edit=untick)
		self.ms_link.refresh_from_db()
		self.assertFalse(self.ms_link.suppressed)

	def test_the_suppression_is_in_history_under_the_editor(self):
		self.inline_post(
			self.editor_a,
			edit=lambda data, prefix, index_of: data.update({f"{prefix}-{index_of[self.ocrevus.pk]}-suppressed": "on"}),
		)
		row = self.ms_link.history.latest("history_date", "history_id")
		self.assertEqual(row.history_type, "~")
		self.assertTrue(row.suppressed)
		self.assertEqual(row.editor_user, self.editor_a)
		self.assertEqual(row.editor_label, "Ana Editor <editor-a@example.com>")
		self.assertEqual(row.via, "admin")

	def test_only_suppressed_is_editable(self):
		inline = self.inline(self.editor_a)
		form = next(f for f in inline.formset.forms if f.instance.pk)
		self.assertEqual(set(form.fields) - {"id", "subject"}, {"suppressed"})

		def forge(data, prefix, index_of):
			i = index_of[self.ocrevus.pk]
			data[f"{prefix}-{i}-matched_on"] = "forged"
			data[f"{prefix}-{i}-medicine"] = str(self.viagra.pk)
			data[f"{prefix}-{i}-suppressed"] = "on"

		self.inline_post(self.editor_a, edit=forge)
		self.ms_link.refresh_from_db()
		self.assertTrue(self.ms_link.suppressed)
		self.assertEqual(self.ms_link.matched_on, "Multiple Sclerosis")
		self.assertEqual(self.ms_link.medicine_id, self.ocrevus.pk)

	def test_there_is_no_add_and_no_delete(self):
		for user in (self.editor_a, self.superuser):
			inline = self.inline(user)
			self.assertFalse(inline.has_add_permission)
			self.assertFalse(inline.has_delete_permission)

	def test_a_forged_delete_or_add_changes_nothing(self):
		before = MedicineSubjectLink.objects.count()

		def forge(data, prefix, index_of):
			data[f"{prefix}-{index_of[self.ocrevus.pk]}-DELETE"] = "on"
			total = int(data[f"{prefix}-TOTAL_FORMS"])
			data[f"{prefix}-TOTAL_FORMS"] = str(total + 1)
			data[f"{prefix}-{total}-medicine"] = str(self.viagra.pk)
			data[f"{prefix}-{total}-matched_on"] = "forged"

		self.inline_post(self.superuser, edit=forge)
		self.assertEqual(MedicineSubjectLink.objects.count(), before)
		self.assertTrue(MedicineSubjectLink.objects.filter(pk=self.ms_link.pk).exists())

	def test_the_matcher_never_restores_a_suppressed_link(self):
		self.inline_post(
			self.editor_a,
			edit=lambda data, prefix, index_of: data.update({f"{prefix}-{index_of[self.ocrevus.pk]}-suppressed": "on"}),
		)
		link_subject(self.ms)
		self.ms_link.refresh_from_db()
		self.assertTrue(self.ms_link.suppressed)

	def test_an_editor_of_another_team_sees_it_read_only_and_cannot_post(self):
		inline = self.inline(self.editor_b)
		self.assertFalse(inline.has_change_permission)
		response = self.inline_post(
			self.editor_a,
			subject=self.team_b_subject,
		)
		self.assertEqual(response.status_code, 403)

	def test_the_inline_checks_the_parent_through_team_scope(self):
		request = RequestFactory().get("/")
		inline = MedicineSubjectLinkInline(Subject, admin.site)
		request.user = self.editor_a
		self.assertTrue(inline.has_change_permission(request, self.ms))
		self.assertFalse(inline.has_change_permission(request, self.team_b_subject))
		request.user = self.superuser
		self.assertTrue(inline.has_change_permission(request, self.team_b_subject))
		self.assertFalse(inline.has_add_permission(request, self.ms))
		self.assertFalse(inline.has_delete_permission(request, self.ms))


class CategorySaveLinkingTests(MedicineLinkAdminTestCase):
	"""Saving an automatic category in the admin links its medicines at once."""

	def form_data(self, response):
		"""``form_data`` plus the hidden ``initial-`` inputs a browser also posts for the
		array fields; without them every array field looks changed."""
		data = form_data(response)
		form = response.context["adminform"].form
		for name, field in form.fields.items():
			if field.show_hidden_initial:
				bound = form[name]
				data[bound.html_initial_name] = field.prepare_value(bound.initial)
		return data

	def edit(self, category, **changes):
		self.client.force_login(self.editor_a)
		url = self.change_url(category)
		response = self.client.get(url)
		self.assertEqual(response.status_code, 200)
		data = self.form_data(response)
		data.update(changes)
		return self.client.post(url, data, follow=True)

	def messages(self, response):
		return [str(m) for m in response.context["messages"]]

	def links(self, category):
		return {
			a.medicine.name: a
			for a in MedicineCategoryAssignment.objects.filter(teamcategory=category).select_related("medicine")
		}

	def test_changing_the_terms_links_the_new_medicines_at_once(self):
		response = self.edit(self.category, category_terms="natalizumab")
		self.assertEqual(response.status_code, 200)
		links = self.links(self.category)
		# Ocrevus (automatic, no longer matching) is gone; Viagra (manual) stays.
		self.assertEqual(set(links), {"Tysabri", "Tyruko", "Natalizumab Elan Pharma", "Viagra"})
		self.assertEqual(links["Tysabri"].source, Source.AUTOMATIC)
		self.assertEqual(links["Tysabri"].matched_on, "natalizumab")
		self.assertIn("3 medicines linked to 'Ocrelizumab'.", self.messages(response))

	def test_the_links_are_pipeline_writes_with_no_editor(self):
		self.edit(self.category, category_terms="natalizumab")
		link = MedicineCategoryAssignment.objects.get(teamcategory=self.category, medicine__name="Tysabri")
		self.assertEqual(MedicineCategoryAssignment.history.filter(id=link.pk).count(), 0)

	def test_a_medicine_only_field_links_without_re_matching_content(self):
		# medicine_terms is not one of the fields rebuild_categories reads.
		with patch("gregory.admin.link_category", wraps=link_category) as linked, \
			patch("gregory.admin.call_command") as rebuilt:
			response = self.edit(self.category, medicine_terms="natalizumab")
		self.assertEqual(response.status_code, 200)
		linked.assert_called_once()
		rebuilt.assert_not_called()
		self.assertEqual(set(self.links(self.category)), {"Tysabri", "Tyruko", "Natalizumab Elan Pharma", "Viagra"})

	def test_a_content_only_field_re_matches_content_without_linking_medicines(self):
		with patch("gregory.admin.link_category") as linked, patch("gregory.admin.call_command") as rebuilt:
			self.edit(self.category, match_scope="title")
		rebuilt.assert_called_once()
		linked.assert_not_called()

	def test_an_unrelated_change_does_not_run_the_matcher(self):
		with patch("gregory.admin.link_category") as linked, patch("gregory.admin.call_command") as rebuilt:
			response = self.edit(self.category, category_description="A new description")
		self.assertEqual(response.status_code, 200)
		linked.assert_not_called()
		rebuilt.assert_not_called()
		self.category.refresh_from_db()
		self.assertEqual(self.category.category_description, "A new description")

	def test_saving_a_manual_category_links_nothing(self):
		self.category.category_type = "manual"
		self.category.save()
		self.edit(self.category, category_type="manual", category_terms="natalizumab")
		self.assertEqual(set(self.links(self.category)), {"Ocrevus", "Viagra"})

	def test_a_failure_warns_and_keeps_the_category_change(self):
		with patch("gregory.admin.link_category", side_effect=RuntimeError("boom")):
			response = self.edit(self.category, category_terms="natalizumab")
		self.assertEqual(response.status_code, 200)
		self.category.refresh_from_db()
		self.assertEqual(self.category.category_terms, ["natalizumab"])
		self.assertTrue(any("will link them on its next run" in m for m in self.messages(response)))
		# The links were not touched.
		self.assertEqual(set(self.links(self.category)), {"Ocrevus", "Viagra"})

	def test_a_new_automatic_category_is_linked_when_created(self):
		self.editor_a.user_permissions.add(Permission.objects.get(codename="add_teamcategory"))
		self.client.force_login(self.editor_a)
		response = self.client.get(reverse("admin:gregory_teamcategory_add"))
		self.assertEqual(response.status_code, 200)
		data = self.form_data(response)
		data.update(
			{
				"team": str(self.team_a.pk),
				"category_name": "Natalizumab new",
				"category_slug": "natalizumab-new",
				"subjects": [str(Subject.objects.get(subject_slug="ms").pk)],
				"category_type": "automatic",
				"modality": CategoryModality.BIOLOGIC_ANTIBODY,
				"category_terms": "natalizumab",
			}
		)
		response = self.client.post(reverse("admin:gregory_teamcategory_add"), data, follow=True)
		self.assertEqual(response.status_code, 200)
		created = TeamCategory.objects.get(category_slug="natalizumab-new")
		self.assertEqual(set(self.links(created)), {"Tysabri", "Tyruko", "Natalizumab Elan Pharma"})


class SubjectLinkPageTests(SubjectLinkAdminTestCase):
	"""The Medicine subject links page, as users who hold only the Editor role."""

	changelist = "admin:gregory_medicinesubjectlink_changelist"

	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		sync_editor_group()
		group = Group.objects.get(name=EDITOR_GROUP)
		cls.role_a = cls.role_user("role-a", cls.org, cls.team_a, group, first_name="Rita", last_name="Role")
		cls.role_b = cls.role_user("role-b", cls.org, cls.team_b, group)
		cls.role_c = cls.role_user("role-c", cls.other_org, cls.team_c, group)
		cls.b_ms_link = MedicineSubjectLink.objects.get(subject=cls.team_b_subject, medicine=cls.ocrevus)
		cls.foreign_ms_link = MedicineSubjectLink.objects.get(subject=cls.foreign_subject, medicine=cls.ocrevus)

	@classmethod
	def role_user(cls, username, org, team, group, **kwargs):
		user = User.objects.create_user(username, f"{username}@example.com", "pw", is_staff=True, **kwargs)
		org.add_user(user)
		team.members.add(user)
		user.groups.add(group)
		return user

	def run_action(self, user, action, *links):
		self.client.force_login(user)
		return self.client.post(
			reverse(self.changelist),
			{"action": action, "_selected_action": [str(link.pk) for link in links]},
			follow=True,
		)

	def change_url(self, link):
		return reverse("admin:gregory_medicinesubjectlink_change", args=[link.pk])

	def test_the_editor_role_can_change_but_not_add_or_delete(self):
		perms = set(self.role_a.get_all_permissions())
		self.assertIn("gregory.view_medicinesubjectlink", perms)
		self.assertIn("gregory.change_medicinesubjectlink", perms)
		self.assertNotIn("gregory.add_medicinesubjectlink", perms)
		self.assertNotIn("gregory.delete_medicinesubjectlink", perms)
		# The Subject page itself stays view-only for the role.
		self.assertNotIn("gregory.change_subject", perms)

	def test_suppress_saves_each_row_so_history_names_the_editor(self):
		before = self.ms_link.history.count()
		response = self.run_action(self.role_a, "suppress_links", self.ms_link)
		self.assertEqual(response.status_code, 200)
		self.ms_link.refresh_from_db()
		self.assertTrue(self.ms_link.suppressed)
		self.assertEqual(self.ms_link.history.count(), before + 1)
		row = self.ms_link.history.latest("history_date", "history_id")
		self.assertEqual(row.history_type, "~")
		self.assertTrue(row.suppressed)
		self.assertEqual(row.editor_user, self.role_a)
		self.assertEqual(row.editor_label, "Rita Role <role-a@example.com>")
		self.assertEqual(row.via, "admin")

	def test_restore_clears_suppressed_and_records_the_editor(self):
		self.ms_link.suppressed = True
		self.ms_link.save()
		self.run_action(self.role_a, "restore_links", self.ms_link)
		self.ms_link.refresh_from_db()
		self.assertFalse(self.ms_link.suppressed)
		row = self.ms_link.history.latest("history_date", "history_id")
		self.assertEqual(row.editor_user, self.role_a)
		self.assertFalse(row.suppressed)

	def test_the_matcher_does_not_bring_a_suppressed_link_back(self):
		self.run_action(self.role_a, "suppress_links", self.ms_link)
		link_subject(self.ms)
		self.ms_link.refresh_from_db()
		self.assertTrue(self.ms_link.suppressed)

	def test_the_change_page_edits_only_suppressed(self):
		self.client.force_login(self.role_a)
		response = self.client.post(
			self.change_url(self.ms_link),
			{"suppressed": "on", "medicine": self.viagra.pk, "subject": self.no_areas.pk, "matched_on": "forged"},
		)
		self.assertEqual(response.status_code, 302)
		self.ms_link.refresh_from_db()
		self.assertTrue(self.ms_link.suppressed)
		self.assertEqual(self.ms_link.medicine, self.ocrevus)
		self.assertEqual(self.ms_link.subject, self.ms)
		self.assertEqual(self.ms_link.matched_on, "Multiple Sclerosis")
		row = self.ms_link.history.latest("history_date", "history_id")
		self.assertEqual(row.editor_user, self.role_a)
		# And back, from the same page: an unticked box restores it.
		self.client.post(self.change_url(self.ms_link), {})
		self.ms_link.refresh_from_db()
		self.assertFalse(self.ms_link.suppressed)

	def test_they_see_but_cannot_change_another_teams_link_in_their_organisation(self):
		self.client.force_login(self.role_a)
		shown = {link.pk for link in self.client.get(reverse(self.changelist)).context["cl"].result_list}
		self.assertIn(self.b_ms_link.pk, shown)
		self.assertEqual(self.client.get(self.change_url(self.b_ms_link)).status_code, 200)
		self.assertEqual(self.client.post(self.change_url(self.b_ms_link), {"suppressed": "on"}).status_code, 403)
		response = self.run_action(self.role_a, "suppress_links", self.ms_link, self.b_ms_link)
		self.ms_link.refresh_from_db()
		self.b_ms_link.refresh_from_db()
		self.assertTrue(self.ms_link.suppressed)
		self.assertFalse(self.b_ms_link.suppressed)
		self.assertContains(response, "belong to another team")

	def test_another_organisations_links_are_invisible(self):
		self.client.force_login(self.role_a)
		shown = {link.pk for link in self.client.get(reverse(self.changelist)).context["cl"].result_list}
		self.assertNotIn(self.foreign_ms_link.pk, shown)
		self.assertTrue(shown)
		# Django answers an object outside the queryset with a redirect, never its content.
		self.assertEqual(self.client.get(self.change_url(self.foreign_ms_link)).status_code, 302)
		self.assertEqual(self.client.post(self.change_url(self.foreign_ms_link), {"suppressed": "on"}).status_code, 302)
		self.foreign_ms_link.refresh_from_db()
		self.assertFalse(self.foreign_ms_link.suppressed)
		# The other organisation's own editor sees theirs, and none of ours.
		self.client.force_login(self.role_c)
		own = {link.pk for link in self.client.get(reverse(self.changelist)).context["cl"].result_list}
		self.assertEqual(
			own, set(MedicineSubjectLink.objects.filter(subject=self.foreign_subject).values_list("pk", flat=True))
		)
		self.assertIn(self.foreign_ms_link.pk, own)

	def test_the_subject_filter_offers_only_the_users_organisations_subjects(self):
		self.client.force_login(self.role_a)
		response = self.client.get(reverse(self.changelist))
		spec = next(s for s in response.context["cl"].filter_specs if "subject" in str(s.title).lower())
		offered = {pk for pk, _ in spec.lookup_choices if pk is not None}
		self.assertIn(self.ms.pk, offered)
		self.assertIn(self.team_b_subject.pk, offered)
		self.assertNotIn(self.foreign_subject.pk, offered)

	def test_the_list_shows_the_ema_status_and_filters_by_suppressed(self):
		self.ms_link.suppressed = True
		self.ms_link.save()
		self.client.force_login(self.role_a)
		response = self.client.get(reverse(self.changelist), {"suppressed__exact": "1"})
		self.assertEqual({link.pk for link in response.context["cl"].result_list}, {self.ms_link.pk})
		self.assertContains(response, "Authorised")

	def test_superuser_sees_every_link(self):
		self.client.force_login(self.superuser)
		response = self.client.get(reverse(self.changelist))
		self.assertEqual(response.context["cl"].result_count, MedicineSubjectLink.objects.count())

	def test_nobody_adds_or_deletes_from_this_page(self):
		self.client.force_login(self.superuser)
		self.assertEqual(self.client.get(reverse("admin:gregory_medicinesubjectlink_add")).status_code, 403)
		url = reverse("admin:gregory_medicinesubjectlink_delete", args=[self.ms_link.pk])
		self.assertEqual(self.client.get(url).status_code, 403)
		self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 403)
		self.assertTrue(MedicineSubjectLink.objects.filter(pk=self.ms_link.pk).exists())
		# The role has no delete permission either, so there is no delete action.
		self.client.force_login(self.role_a)
		response = self.client.get(reverse(self.changelist))
		self.assertNotIn("delete_selected", dict(response.context["action_form"].fields["action"].choices))
