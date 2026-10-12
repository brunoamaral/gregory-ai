"""
Tests for the sources, teams and subjects fields on the Articles and Trials
admin forms (gregory.admin.OrganizationScopedRelationsFormMixin).

A staff member may only pick, or post the id of, their own organisations'
sources, teams and subjects. Content can be shared between organisations, so it
may already link another organisation's rows: those links are hidden from staff
and kept when they save. Superusers see and can change everything.

Forms are built through each admin's own get_form(), so these also cover the
admin passing the request to its form.

Run with:
    docker exec gregory python manage.py test gregory.tests.test_admin_relation_scope
"""

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase
from organizations.models import Organization, OrganizationUser

from gregory.admin import ArticleAdmin, TrialAdmin
from gregory.models import Articles, Sources, Subject, Team, Trials

User = get_user_model()

RELATIONS = ("sources", "teams", "subjects")


class _RelationScopeTests:
	"""Cases shared by both admins. Subclasses set the admin, the content model
	and the `source_for` of its sources, and create the content row in
	make_content()."""

	admin_class = None
	content_model = None
	source_for = None

	def make_content(self):
		raise NotImplementedError

	def setUp(self):
		self.org_a = Organization.objects.create(name="Org A", slug="relscope-org-a")
		self.org_b = Organization.objects.create(name="Org B", slug="relscope-org-b")
		team_a = Team.objects.create(organization=self.org_a, name="Team A", slug="relscope-a")
		team_a2 = Team.objects.create(organization=self.org_a, name="Team A2", slug="relscope-a2")
		team_b = Team.objects.create(organization=self.org_b, name="Team B", slug="relscope-b")
		# Per relation: own row, a second own row, and another organisation's row.
		self.own, self.own2, self.foreign = {}, {}, {}
		for name, row in (("own", team_a), ("own2", team_a2), ("foreign", team_b)):
			getattr(self, name)["teams"] = row
			getattr(self, name)["sources"] = Sources.objects.create(
				name=f"Relscope source {row.name}", source_for=self.source_for, team=row
			)
			getattr(self, name)["subjects"] = Subject.objects.create(
				subject_name=f"Relscope subject {row.name}",
				subject_slug=f"relscope-subject-{row.slug}",
				team=row,
			)
		self.content = self.make_content()

		# Staff member of org A only, with the content model's permissions.
		self.staff = User.objects.create_user(
			username="relscope-staff", password="pw", is_staff=True
		)
		OrganizationUser.objects.create(organization=self.org_a, user=self.staff)
		self.staff.user_permissions.add(
			*Permission.objects.filter(
				content_type__app_label="gregory",
				content_type__model=self.content_model._meta.model_name,
			)
		)
		self.superuser = User.objects.create_superuser(
			"relscope-root", "root@example.com", "pw"
		)
		self.model_admin = self.admin_class(self.content_model, admin.site)

	def _link(self, *groups):
		"""Link the content to every relation's row in each of `groups`."""
		for group in groups:
			for name in RELATIONS:
				getattr(self.content, name).add(group[name])

	def _form(self, user, data=None):
		request = RequestFactory().get("/")
		request.user = user
		form_class = self.model_admin.get_form(
			request, self.content, change=True, fields=list(RELATIONS)
		)
		if data is None:
			return form_class(instance=self.content)
		return form_class(data, instance=self.content)

	def _post(self, *groups):
		"""Form data choosing, for every relation, the rows in `groups`."""
		return {name: [group[name].pk for group in groups] for name in RELATIONS}

	def _linked(self, name):
		return set(getattr(self.content, name).all())

	def test_staff_choices_exclude_other_organisations_rows(self):
		form = self._form(self.staff)
		for name in RELATIONS:
			with self.subTest(name):
				self.assertIn(self.own[name], form.fields[name].queryset)
				self.assertNotIn(self.foreign[name], form.fields[name].queryset)

	def test_superuser_choices_include_every_organisations_rows(self):
		form = self._form(self.superuser)
		for name in RELATIONS:
			with self.subTest(name):
				self.assertIn(self.own[name], form.fields[name].queryset)
				self.assertIn(self.foreign[name], form.fields[name].queryset)

	def test_staff_cannot_post_another_organisations_rows(self):
		for name in RELATIONS:
			with self.subTest(name):
				data = self._post(self.own)
				data[name] = [self.own[name].pk, self.foreign[name].pk]
				form = self._form(self.staff, data)
				self.assertFalse(form.is_valid())
				self.assertIn(name, form.errors)

	def test_staff_save_keeps_another_organisations_links(self):
		self._link(self.own, self.foreign)
		# Staff are shown only their own links; they add a second own row.
		form = self._form(self.staff, self._post(self.own, self.own2))
		self.assertTrue(form.is_valid(), form.errors)
		form.save()
		for name in RELATIONS:
			with self.subTest(name):
				self.assertEqual(
					self._linked(name),
					{self.own[name], self.own2[name], self.foreign[name]},
				)

	def test_staff_can_remove_their_own_links_only(self):
		self._link(self.own, self.foreign)
		form = self._form(self.staff, self._post())
		self.assertTrue(form.is_valid(), form.errors)
		form.save()
		for name in RELATIONS:
			with self.subTest(name):
				self.assertEqual(self._linked(name), {self.foreign[name]})

	def test_another_organisations_links_satisfy_required_fields(self):
		"""Teams and subjects are required. Content linked only to another
		organisation's team and subject can still be saved without staff
		picking one of their own."""
		self._link(self.foreign)
		form = self._form(self.staff, self._post())
		self.assertTrue(form.is_valid(), form.errors)

	def test_staff_still_need_a_team_and_subject_on_content_without_any(self):
		form = self._form(self.staff, self._post())
		self.assertFalse(form.is_valid())
		self.assertIn("teams", form.errors)
		self.assertIn("subjects", form.errors)

	def test_hidden_links_do_not_count_as_a_change(self):
		self._link(self.own, self.foreign)
		form = self._form(self.staff, self._post(self.own))
		self.assertTrue(form.is_valid(), form.errors)
		self.assertEqual(set(form.changed_data) & set(RELATIONS), set())

	def test_superuser_can_remove_another_organisations_links(self):
		self._link(self.own, self.foreign)
		form = self._form(self.superuser, self._post(self.own))
		self.assertTrue(form.is_valid(), form.errors)
		form.save()
		for name in RELATIONS:
			with self.subTest(name):
				self.assertEqual(self._linked(name), {self.own[name]})


class ArticleRelationScopeTests(_RelationScopeTests, TestCase):
	admin_class = ArticleAdmin
	content_model = Articles
	source_for = "science paper"

	def make_content(self):
		return Articles.objects.create(
			title="Relscope article", link="https://example.com/relscope-article"
		)


class TrialRelationScopeTests(_RelationScopeTests, TestCase):
	admin_class = TrialAdmin
	content_model = Trials
	source_for = "trials"

	def make_content(self):
		return Trials.objects.create(
			title="Relscope trial", link="https://example.com/relscope-trial"
		)
