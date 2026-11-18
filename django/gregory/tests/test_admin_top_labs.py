"""The Top labs section on subject and category change pages: superusers only, added per
request, never visible to editors, and constant in queries."""

from django.contrib.admin.sites import site as admin_site
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from organizations.models import OrganizationUser

from gregory.admin_labs import HideLabLinksOnDeleteMixin, TopLabsAdminMixin
from gregory.admin import TeamCategoryAdmin
from gregory.models import (
	Institution,
	Lab,
	LabCategory,
	LabSubject,
	Subject,
	Team,
	TeamCategory,
)
from gregory.roles import EDITOR_GROUP, sync_editor_group
from gregory.tests.test_admin_labs import LabAdminCase, author, make_lab, post_data

User = get_user_model()
LINK_TABLES = ("lab_subjects", "lab_categories", '"labs"')


class TopLabsCase(LabAdminCase):
	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		cls.category = TeamCategory.objects.create(team=cls.team, category_name="Cat", category_slug="cat")
		# A second team in the same organisation, for the editor to view but not edit.
		cls.team2 = Team.objects.create(organization=cls.org, name="Other Team", slug="other-team")
		cls.subject2 = Subject.objects.create(subject_name="Theirs", subject_slug="theirs", team=cls.team2)
		cls.category2 = TeamCategory.objects.create(
			team=cls.team2, category_name="Theirs cat", category_slug="theirs-cat"
		)
		sync_editor_group()
		cls.editor = User.objects.create_user("editor", "e@example.org", "pw", is_staff=True)
		cls.editor.groups.add(Group.objects.get(name=EDITOR_GROUP))
		OrganizationUser.objects.create(organization=cls.org, user=cls.editor)
		cls.team.members.add(cls.editor)

	def kinds(self):
		"""(link model, topic field, own topic, other team's topic, admin url pattern)"""
		return [
			(LabSubject, "subject", self.subject, self.subject2, "admin:gregory_subject_%s"),
			(LabCategory, "category", self.category, self.category2, "admin:gregory_teamcategory_%s"),
		]

	def link(self, model, field, topic, lab, **kwargs):
		kwargs.setdefault("article_count", 3)
		return model.objects.create(lab=lab, **{field: topic}, **kwargs)

	def labs(self, n, start=1):
		return [make_lab(author(start + i)) for i in range(n)]

	def page(self, name, topic):
		return self.client.get(reverse(name % "change", args=[topic.pk]))


class TopLabsSectionTests(TopLabsCase):
	def setUp(self):
		self.login()

	def test_superuser_sees_the_section_with_the_columns_and_total(self):
		for n, (model, field, topic, _other, name) in enumerate(self.kinds()):
			inst = Institution.objects.create(openalex_id=f"I{field}", name=f"Inst {field}")
			lab = make_lab(author(1 + n), institution=inst, name=f"Lab of {field}")
			self.link(
				model, field, topic, lab,
				article_count=9, relevant_count=4, recent_article_count=2, latest_article_year=2024,
			)
			response = self.page(name, topic)
			self.assertEqual(response.status_code, 200)
			self.assertContains(response, "Top labs")
			self.assertContains(response, f"Lab of {field}")
			self.assertContains(response, f"Inst {field}")
			self.assertContains(response, lab.head.full_name)
			self.assertContains(response, "<td>2024</td>", html=True)
			self.assertContains(response, "1 linked lab in total")
			self.assertContains(
				response, f'{reverse("admin:gregory_lab_changelist")}?{field}={topic.pk}'
			)
			self.assertContains(response, reverse("admin:gregory_lab_change", args=[lab.pk]))

	def test_a_head_with_no_stored_full_name_shows_their_name(self):
		head = author(70)
		lab = make_lab(head)
		type(head).objects.filter(pk=head.pk).update(full_name="")
		LabSubject.objects.create(lab=lab, subject=self.subject, article_count=3)
		response = self.client.get(reverse("admin:gregory_subject_change", args=[self.subject.pk]))
		self.assertContains(response, f"<td>{head.given_name} {head.family_name}</td>", html=True)

	def test_the_section_is_its_own_fieldset(self):
		for _model, _field, topic, _other, name in self.kinds():
			response = self.page(name, topic)
			fieldsets = response.context["adminform"].fieldsets
			titles = [title for title, _ in fieldsets]
			self.assertEqual(titles.count("Top labs"), 1)
			self.assertEqual(titles[-1], "Top labs")
			for _title, options in fieldsets[:-1]:
				self.assertNotIn("top_labs_block", str(options["fields"]))

	def test_empty_topic_says_so(self):
		for _model, _field, topic, _other, name in self.kinds():
			self.assertContains(self.page(name, topic), "No linked labs yet.")

	def test_add_page_has_no_section(self):
		for _model, _field, _topic, _other, name in self.kinds():
			response = self.client.get(reverse(name % "add"))
			self.assertEqual(response.status_code, 200)
			self.assertNotContains(response, "Top labs")

	def test_limit_ranking_and_hidden_links(self):
		for n, (model, field, topic, _other, name) in enumerate(self.kinds()):
			labs = self.labs(28, start=100 + 100 * n)
			# Ranked by relevant, then papers, then pk.
			for i, lab in enumerate(labs[:27]):
				self.link(model, field, topic, lab, relevant_count=i, article_count=3)
			self.link(model, field, topic, labs[27], is_hidden=True, relevant_count=999)
			content = self.page(name, topic).content.decode()
			section = content[content.index("<table>") :]
			self.assertEqual(section.count("<tr><td>"), 25)
			self.assertIn("27 linked labs in total", section)
			self.assertNotIn(f"/lab/{labs[27].pk}/", section)
			# Highest relevant first; the two lowest are cut.
			self.assertLess(section.index(f"/lab/{labs[26].pk}/"), section.index(f"/lab/{labs[25].pk}/"))
			self.assertNotIn(f"/lab/{labs[0].pk}/", section)
			self.assertNotIn(f"/lab/{labs[1].pk}/", section)
			self.assertIn(f"/lab/{labs[2].pk}/", section)

	def test_ties_break_on_papers_then_pk(self):
		for n, (model, field, topic, _other, name) in enumerate(self.kinds()):
			a, b, c = self.labs(3, start=300 + 100 * n)
			self.link(model, field, topic, a, relevant_count=1, article_count=5)
			self.link(model, field, topic, b, relevant_count=1, article_count=7)
			self.link(model, field, topic, c, relevant_count=1, article_count=5)
			section = self.page(name, topic).content.decode()
			order = [section.index(f"/lab/{lab.pk}/") for lab in (b, a, c)]
			self.assertEqual(order, sorted(order))

	def test_hostile_names_are_escaped(self):
		for n, (model, field, topic, _other, name) in enumerate(self.kinds()):
			inst = Institution.objects.create(openalex_id=f"H{field}", name="<script>alert(2)</script>")
			lab = make_lab(author(500 + n), institution=inst, name="<img src=x onerror=alert(1)>")
			self.link(model, field, topic, lab)
			content = self.page(name, topic).content.decode()
			self.assertNotIn("<img src=x", content)
			self.assertNotIn("<script>alert(2)", content)
			self.assertIn("&lt;img src=x onerror=alert(1)&gt;", content)
			self.assertIn("&lt;script&gt;alert(2)&lt;/script&gt;", content)

	def test_a_lab_with_no_head_does_not_crash(self):
		for _model, field, topic, _other, name in self.kinds():
			model = LabSubject if field == "subject" else LabCategory
			lab = Lab.objects.create(name="Headless lab")
			self.link(model, field, topic, lab)
			response = self.page(name, topic)
			self.assertEqual(response.status_code, 200)
			self.assertContains(response, "Headless lab")

	def test_query_count_does_not_grow_with_rows(self):
		for n, (model, field, topic, _other, name) in enumerate(self.kinds()):
			url = reverse(name % "change", args=[topic.pk])
			for lab in self.labs(2, start=600 + 100 * n):
				self.link(model, field, topic, lab)
			self.client.get(url)  # warm caches
			with CaptureQueriesContext(connection) as small:
				self.client.get(url)
			for lab in self.labs(20, start=800 + 100 * n):
				self.link(model, field, topic, lab)
			with CaptureQueriesContext(connection) as large:
				self.client.get(url)
			self.assertEqual(len(small), len(large), field)


class TopLabsEditorTests(TopLabsCase):
	def setUp(self):
		lab = make_lab(author(1), name="Secret Lab Name")
		for model, field, topic, other, _name in self.kinds():
			self.link(model, field, topic, lab)
			self.link(model, field, other, lab)
		self.login(self.editor)

	def lab_queries(self, queries):
		return [q["sql"] for q in queries if any(t in q["sql"] for t in LINK_TABLES)]

	def test_editor_sees_no_section_and_runs_no_lab_query_on_own_and_other_teams(self):
		for _model, _field, topic, other, name in self.kinds():
			for obj in (topic, other):
				with CaptureQueriesContext(connection) as ctx:
					response = self.page(name, obj)
				self.assertEqual(response.status_code, 200)
				self.assertNotContains(response, "Top labs")
				self.assertNotContains(response, "Secret Lab Name")
				self.assertEqual(self.lab_queries(ctx.captured_queries), [])

	def test_editor_can_still_save_their_own_pages_with_no_lab_queries(self):
		# The Editor role only views subjects; a staff member granted change can save one.
		self.editor.user_permissions.add(Permission.objects.get(codename="change_subject"))
		self.login(self.editor)
		url = reverse("admin:gregory_subject_change", args=[self.subject.pk])
		data = post_data(self.client.get(url), subject_name="Renamed by editor")
		with CaptureQueriesContext(connection) as ctx:
			saved = self.client.post(url, data)
		self.assertEqual(saved.status_code, 302)
		self.subject.refresh_from_db()
		self.assertEqual(self.subject.subject_name, "Renamed by editor")
		self.assertEqual(self.lab_queries(ctx.captured_queries), [])

		url = reverse("admin:gregory_teamcategory_change", args=[self.category.pk])
		data = post_data(
			self.client.get(url),
			category_description="Edited",
			subjects=[self.subject.pk],
			category_terms="term",
		)
		with CaptureQueriesContext(connection) as ctx:
			saved = self.client.post(url, data)
		errors = saved.context["adminform"].form.errors if saved.status_code == 200 else None
		self.assertEqual(saved.status_code, 302, errors)
		self.category.refresh_from_db()
		self.assertEqual(self.category.category_description, "Edited")
		self.assertEqual(self.lab_queries(ctx.captured_queries), [])

	def test_editor_cannot_save_another_teams_page(self):
		url = reverse("admin:gregory_subject_change", args=[self.subject2.pk])
		response = self.client.post(url, {"subject_name": "Hijacked"})
		self.assertIn(response.status_code, (302, 403))
		self.subject2.refresh_from_db()
		self.assertEqual(self.subject2.subject_name, "Theirs")

	def test_superuser_save_still_works_and_keeps_links(self):
		self.login()
		url = reverse("admin:gregory_subject_change", args=[self.subject.pk])
		data = post_data(self.client.get(url), subject_name="Renamed by root")
		self.assertEqual(self.client.post(url, data).status_code, 302)
		self.assertEqual(LabSubject.objects.filter(subject=self.subject).count(), 1)


class TopLabsIsolationTests(TopLabsCase):
	def test_nothing_leaks_between_requests_on_one_admin_instance(self):
		factory = RequestFactory()
		for topic in (self.subject, self.category):
			model_admin = admin_site._registry[type(topic)]
			before_ro = list(model_admin.readonly_fields)
			before_fs = model_admin.fieldsets
			root = factory.get("/")
			root.user = self.superuser
			editor = factory.get("/")
			editor.user = self.editor
			self.assertIn("top_labs_block", model_admin.get_readonly_fields(root, topic))
			self.assertNotIn("top_labs_block", model_admin.get_readonly_fields(editor, topic))
			self.assertNotIn("top_labs_block", model_admin.get_readonly_fields(root, None))
			self.assertIn("Top labs", [t for t, _ in model_admin.get_fieldsets(root, topic)])
			for obj in (topic, None):
				for request in (editor, root) if obj is None else (editor,):
					fieldsets = model_admin.get_fieldsets(request, obj)
					self.assertNotIn("top_labs_block", str(fieldsets))
			# An editor request after a superuser request is clean again.
			self.assertNotIn("top_labs_block", str(model_admin.get_fieldsets(editor, topic)))
			self.assertEqual(list(model_admin.readonly_fields), before_ro)
			self.assertEqual(model_admin.fieldsets, before_fs)
			self.assertNotIn("top_labs_block", str(model_admin.fieldsets))

	def test_both_mixins_run_for_categories(self):
		mro = TeamCategoryAdmin.__mro__
		self.assertLess(mro.index(TopLabsAdminMixin), mro.index(HideLabLinksOnDeleteMixin))
		lab = make_lab(author(1))
		LabCategory.objects.create(lab=lab, category=self.category, article_count=3)
		url = reverse("admin:gregory_teamcategory_delete", args=[self.category.pk])
		self.login()
		self.assertContains(self.client.get(url), "Lab category: ")
		self.login(self.editor)
		response = self.client.get(url)
		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, "Lab category: ")
