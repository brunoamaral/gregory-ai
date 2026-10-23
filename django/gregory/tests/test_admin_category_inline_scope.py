"""
Tests for the category-assignment inlines on the Articles, Trials and Patents
admin pages (gregory.admin._BaseCategoryAssignmentInline and its subclasses).

The TeamCategory autocomplete endpoint is scoped to the user's organisations,
but the inline's form field validates against its own queryset. These pin down
that a staff member can neither pick nor forge another organisation's category,
and that another organisation's assignment on shared content is hidden from
them and left alone when they save the page.

Run with:
    docker exec gregory python manage.py test gregory.tests.test_admin_category_inline_scope
"""

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase
from organizations.models import Organization, OrganizationUser

from gregory.admin import (
	ArticleCategoryAssignmentInline,
	PatentCategoryAssignmentInline,
	TrialCategoryAssignmentInline,
)
from gregory.models import (
	ArticleCategoryAssignment,
	Articles,
	PatentCategoryAssignment,
	Patents,
	Sources,
	Team,
	TeamCategory,
	TrialCategoryAssignment,
	Trials,
)

User = get_user_model()


class _CategoryInlineScopeTests:
	"""Cases shared by the inlines. Subclasses set the inline, the content
	model, the assignment model and the assignment's FK to the content row, and
	create the content row in make_content()."""

	inline_class = None
	content_model = None
	assignment_model = None
	parent_field = None

	def make_content(self):
		raise NotImplementedError

	def setUp(self):
		self.org_a = Organization.objects.create(name="Org A", slug="catinline-org-a")
		self.org_b = Organization.objects.create(name="Org B", slug="catinline-org-b")
		self.team_a = Team.objects.create(
			organization=self.org_a, name="Team A", slug="catinline-team-a"
		)
		self.team_b = Team.objects.create(
			organization=self.org_b, name="Team B", slug="catinline-team-b"
		)
		self.category_a = TeamCategory.objects.create(
			team=self.team_a, category_name="Catinline own", category_terms=["a"]
		)
		self.category_a2 = TeamCategory.objects.create(
			team=self.team_a, category_name="Catinline own two", category_terms=["a2"]
		)
		self.category_b = TeamCategory.objects.create(
			team=self.team_b, category_name="Catinline foreign", category_terms=["b"]
		)
		self.content = self.make_content()

		# Staff member of org A only. Without add/change permission on the
		# assignment model the inline treats every row as unchanged and skips
		# validating it, which would let the forged-category case pass vacuously.
		self.staff = User.objects.create_user(
			username="catinline-staff", password="pw", is_staff=True
		)
		OrganizationUser.objects.create(organization=self.org_a, user=self.staff)
		# Editing is limited to the user's teams (gregory.team_scope).
		self.team_a.members.add(self.staff)
		self.staff.user_permissions.add(
			*Permission.objects.filter(
				content_type__app_label="gregory",
				content_type__model__in=[
					self.content_model._meta.model_name,
					self.assignment_model._meta.model_name,
				],
			)
		)
		self.superuser = User.objects.create_superuser(
			"catinline-root", "root@example.com", "pw"
		)
		self.inline = self.inline_class(self.content_model, admin.site)

	def _request(self, user):
		request = RequestFactory().get("/")
		request.user = user
		return request

	def _assign(self, category):
		return self.assignment_model.objects.create(
			**{self.parent_field: self.content, "teamcategory": category}
		)

	def _shown_categories(self, user):
		"""Categories of the assignment rows the inline shows `user`."""
		return {
			assignment.teamcategory
			for assignment in self.inline.get_queryset(self._request(user)).filter(
				**{self.parent_field: self.content}
			)
		}

	def _submit(self, user, new_categories=()):
		"""Bind the inline's formset the way the admin change view does when
		`user` submits the page: every row they were shown, unchanged, plus one
		new row per category in `new_categories`."""
		request = self._request(user)
		formset_class = self.inline.get_formset(request, self.content)
		prefix = formset_class.get_default_prefix()
		queryset = self.inline.get_queryset(request)
		shown = formset_class(instance=self.content, prefix=prefix, queryset=queryset)
		rows = [
			(form.instance.pk, form.instance.teamcategory_id)
			for form in shown.initial_forms
		]
		rows += [(None, category.pk) for category in new_categories]
		data = {
			f"{prefix}-TOTAL_FORMS": str(len(rows)),
			f"{prefix}-INITIAL_FORMS": str(len(shown.initial_forms)),
			f"{prefix}-MIN_NUM_FORMS": "0",
			f"{prefix}-MAX_NUM_FORMS": "1000",
		}
		for i, (pk, category_pk) in enumerate(rows):
			data[f"{prefix}-{i}-id"] = str(pk or "")
			data[f"{prefix}-{i}-{self.parent_field}"] = str(self.content.pk)
			data[f"{prefix}-{i}-teamcategory"] = str(category_pk)
			data[f"{prefix}-{i}-source"] = "manual"
		return formset_class(data, instance=self.content, prefix=prefix, queryset=queryset)

	def test_category_field_limits_staff_to_their_organisations_categories(self):
		field = self.inline.formfield_for_foreignkey(
			self.assignment_model._meta.get_field("teamcategory"),
			self._request(self.staff),
		)
		self.assertIn(self.category_a, field.queryset)
		self.assertNotIn(self.category_b, field.queryset)

	def test_category_field_offers_superusers_every_category(self):
		field = self.inline.formfield_for_foreignkey(
			self.assignment_model._meta.get_field("teamcategory"),
			self._request(self.superuser),
		)
		self.assertIn(self.category_a, field.queryset)
		self.assertIn(self.category_b, field.queryset)

	def test_staff_cannot_post_another_organisations_category(self):
		formset = self._submit(self.staff, [self.category_b])
		self.assertFalse(formset.is_valid())
		self.assertIn("teamcategory", formset.forms[-1].errors)
		self.assertFalse(
			self.assignment_model.objects.filter(teamcategory=self.category_b).exists()
		)

	def test_staff_can_assign_their_own_category(self):
		formset = self._submit(self.staff, [self.category_a])
		self.assertTrue(formset.is_valid(), formset.errors)
		formset.save()
		self.assertEqual(self._shown_categories(self.superuser), {self.category_a})

	def test_superuser_can_assign_any_organisations_category(self):
		formset = self._submit(self.superuser, [self.category_b])
		self.assertTrue(formset.is_valid(), formset.errors)
		formset.save()
		self.assertEqual(self._shown_categories(self.superuser), {self.category_b})

	def test_staff_only_see_their_organisations_assignments(self):
		self._assign(self.category_a)
		self._assign(self.category_b)
		self.assertEqual(self._shown_categories(self.staff), {self.category_a})
		self.assertEqual(
			self._shown_categories(self.superuser), {self.category_a, self.category_b}
		)

	def test_staff_can_save_content_carrying_another_organisations_assignment(self):
		"""Content can be shared between organisations, so it can carry another
		organisation's category. If staff were shown that row, it would fail the
		scoped field's validation and block every save of the page."""
		self._assign(self.category_a)
		foreign = self._assign(self.category_b)
		formset = self._submit(self.staff, [self.category_a2])
		self.assertTrue(formset.is_valid(), formset.errors)
		formset.save()
		self.assertTrue(self.assignment_model.objects.filter(pk=foreign.pk).exists())
		self.assertEqual(
			self._shown_categories(self.superuser),
			{self.category_a, self.category_a2, self.category_b},
		)


class ArticleCategoryInlineScopeTests(_CategoryInlineScopeTests, TestCase):
	inline_class = ArticleCategoryAssignmentInline
	content_model = Articles
	assignment_model = ArticleCategoryAssignment
	parent_field = "articles"

	def make_content(self):
		article = Articles.objects.create(
			title="Shared article", link="https://example.com/catinline-article"
		)
		# Brought in by a source of each organisation, so both can see it.
		for team in (self.team_a, self.team_b):
			article.sources.add(
				Sources.objects.create(
					name=f"Source {team.name}", source_for="science paper", team=team
				)
			)
		return article


class TrialCategoryInlineScopeTests(_CategoryInlineScopeTests, TestCase):
	inline_class = TrialCategoryAssignmentInline
	content_model = Trials
	assignment_model = TrialCategoryAssignment
	parent_field = "trials"

	def make_content(self):
		trial = Trials.objects.create(
			title="Shared trial", link="https://example.com/catinline-trial"
		)
		for team in (self.team_a, self.team_b):
			trial.sources.add(
				Sources.objects.create(
					name=f"Trial source {team.name}", source_for="trials", team=team
				)
			)
		return trial


class PatentCategoryInlineScopeTests(_CategoryInlineScopeTests, TestCase):
	inline_class = PatentCategoryAssignmentInline
	content_model = Patents
	assignment_model = PatentCategoryAssignment
	parent_field = "patents"

	def make_content(self):
		patent = Patents.objects.create(family_id="catinline-1", title="Shared patent")
		for team in (self.team_a, self.team_b):
			patent.sources.add(
				Sources.objects.create(
					name=f"Patent source {team.name}",
					source_for="patents",
					method="epo_ops",
					team=team,
				)
			)
		return patent
