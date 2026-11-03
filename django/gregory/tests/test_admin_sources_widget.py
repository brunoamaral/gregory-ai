"""
Tests for the grouped sources checkboxes on the Articles and Trials admin
(gregory.admin.GroupedSourcesFormMixin, gregory.widgets.GroupedSourceCheckboxSelectMultiple).

The widget only changes how `sources` is displayed: checkboxes grouped by team
and subject, a summary of the linked sources, and groups with a linked source
open. The team scoping of TeamScopedRelationsFormMixin must hold as before:
staff see only their own teams' sources, links to other teams' sources stay
hidden and are kept on save. Patents keep filter_horizontal.

Run with:
    pytest gregory/tests/test_admin_sources_widget.py
"""

import re

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.forms import modelform_factory
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from organizations.models import Organization, OrganizationUser

from gregory.admin import (
	SOURCES_HELP_TEXT,
	ArticleAdmin,
	PatentAdmin,
	TrialAdmin,
	grouped_source_choices,
)
from gregory.models import Articles, Patents, Sources, Subject, Team, Trials
from gregory.widgets import GroupedSourceCheckboxSelectMultiple

User = get_user_model()


def group_labels(form):
	return [label for label, _options in form.fields["sources"].widget.choices]


def details_tags(html):
	"""{group summary text: the opening <details> tag} for a rendered field."""
	return {
		summary.strip(): tag
		for tag, summary in re.findall(
			r"(<details[^>]*>)\s*<summary>([^<]*)<span", html
		)
	}


class _SourcesWidgetTests:
	"""Cases shared by the Articles and Trials admins. Subclasses set the admin,
	the content model and the `source_for` of its sources."""

	admin_class = None
	content_model = None
	source_for = None
	change_url_name = None

	def make_content(self):
		raise NotImplementedError

	def setUp(self):
		self.org_a = Organization.objects.create(name="Org A", slug="srcw-org-a")
		self.org_b = Organization.objects.create(name="Org B", slug="srcw-org-b")
		self.team_a1 = Team.objects.create(organization=self.org_a, name="Team A1", slug="srcw-a1")
		self.team_a2 = Team.objects.create(organization=self.org_a, name="Team A2", slug="srcw-a2")
		self.team_b = Team.objects.create(organization=self.org_b, name="Team B", slug="srcw-b")
		self.s1 = self._subject("S1", self.team_a1)
		self.s2 = self._subject("S2", self.team_a2)
		self.s3 = self._subject("S3", self.team_b)
		self.zeta = self._source("Zeta", self.team_a1, self.s1)
		self.alpha = self._source("Alpha", self.team_a1, self.s1)
		self.nosubj = self._source("NoSubj", self.team_a1, None)
		self.beta = self._source("Beta", self.team_a2, self.s2)
		self.foreign = self._source("Foreign", self.team_b, self.s3)
		self.content = self.make_content()
		self.content.sources.add(self.alpha, self.beta, self.foreign)

		# Staff member of org A, team A1 only, with the content model's permissions.
		self.staff = User.objects.create_user(username="srcw-staff", password="pw", is_staff=True)
		OrganizationUser.objects.create(organization=self.org_a, user=self.staff)
		self.team_a1.members.add(self.staff)
		self.staff.user_permissions.add(
			*Permission.objects.filter(
				content_type__app_label="gregory",
				content_type__model=self.content_model._meta.model_name,
			)
		)
		self.superuser = User.objects.create_superuser("srcw-root", "root@example.com", "pw")
		self.model_admin = self.admin_class(self.content_model, admin.site)

	def _subject(self, name, team):
		return Subject.objects.create(
			subject_name=name, subject_slug=f"srcw-{name.lower()}-{self.source_for[:3]}", team=team
		)

	def _source(self, name, team, subject):
		return Sources.objects.create(
			name=name, source_for=self.source_for, team=team, subject=subject
		)

	def _form(self, user, data=None):
		"""The admin's form class, built directly, as in test_admin_relation_scope."""
		request = RequestFactory().get("/")
		request.user = user
		form_class = modelform_factory(
			self.content_model, form=self.model_admin.form, fields=["sources"]
		)
		if data is None:
			return form_class(instance=self.content, request=request)
		return form_class(data, instance=self.content, request=request)

	def _linked(self):
		return set(self.content.sources.all())

	# Widget and grouping

	def test_sources_use_the_grouped_widget_without_the_ctrl_help_text(self):
		field = self._form(self.superuser).fields["sources"]
		self.assertIsInstance(field.widget, GroupedSourceCheckboxSelectMultiple)
		self.assertEqual(field.help_text, SOURCES_HELP_TEXT)

	def test_groups_are_ordered_by_team_then_subject_and_sources_by_name(self):
		form = self._form(self.superuser)
		self.assertEqual(
			group_labels(form),
			["Team A1 · S1", "Team A1 · No subject", "Team A2 · S2", "Team B · S3"],
		)
		first_group = form.fields["sources"].widget.choices[0][1]
		self.assertEqual([label for _pk, label in first_group], ["Alpha", "Zeta"])

	def test_teams_with_the_same_name_stay_in_separate_groups(self):
		org_c = Organization.objects.create(name="Org C", slug="srcw-org-c")
		lab_a = Team.objects.create(organization=self.org_a, name="Lab", slug="srcw-lab-a")
		lab_c = Team.objects.create(organization=org_c, name="Lab", slug="srcw-lab-c")
		lab_a_source = self._source("Lab A source", lab_a, self._subject("Same", lab_a))
		lab_c_source = self._source("Lab C source", lab_c, self._subject("Same2", lab_c))
		groups = [
			[pk for pk, _label in options]
			for label, options in self._form(self.superuser).fields["sources"].widget.choices
			if label.startswith("Lab ·")
		]
		self.assertEqual(sorted(groups), sorted([[lab_a_source.pk], [lab_c_source.pk]]))

	def test_a_source_without_a_team_goes_last(self):
		self._source("Teamless", None, None)
		self.assertEqual(group_labels(self._form(self.superuser))[-1], "No team · No subject")

	def test_choices_take_one_query(self):
		self.assertEqual(len(grouped_source_choices(Sources.objects.all())), 4)
		with CaptureQueriesContext(connection) as few:
			grouped_source_choices(Sources.objects.all())
		for i in range(15):
			self._source(f"Extra {i}", self.team_a2 if i % 2 else self.team_b, None)
		with CaptureQueriesContext(connection) as many:
			grouped_source_choices(Sources.objects.all())
		self.assertEqual(len(few), 1)
		self.assertEqual(len(many), 1)

	# Rendering

	def test_linked_sources_are_ticked_and_summarised_in_display_order(self):
		html = str(self._form(self.superuser)["sources"])
		self.assertIn("<strong>Linked (3):</strong> Alpha, Beta, Foreign", html)
		for source in (self.alpha, self.beta, self.foreign):
			self.assertRegex(html, rf'<input[^>]*value="{source.pk}"[^>]*checked')
		for source in (self.zeta, self.nosubj):
			self.assertRegex(html, rf'<input[^>]*value="{source.pk}"')
			self.assertNotRegex(html, rf'<input[^>]*value="{source.pk}"[^>]*checked')

	def test_groups_with_a_linked_source_start_open(self):
		tags = details_tags(str(self._form(self.superuser)["sources"]))
		self.assertEqual(
			{label: "open" in tag for label, tag in tags.items()},
			{
				"Team A1 · S1": True,
				"Team A1 · No subject": False,
				"Team A2 · S2": True,
				"Team B · S3": True,
			},
		)

	def test_summary_says_none_when_nothing_is_linked(self):
		self.content.sources.clear()
		html = str(self._form(self.superuser)["sources"])
		self.assertIn("Linked: none", html)
		self.assertNotIn(" open", html)

	def test_source_names_are_escaped(self):
		risky = self._source("<b>x</b>", self.team_a1, self.s1)
		self.content.sources.add(risky)
		html = str(self._form(self.superuser)["sources"])
		self.assertNotIn("<b>x</b>", html)
		self.assertGreaterEqual(html.count("&lt;b&gt;x&lt;/b&gt;"), 2)  # summary and label

	# Staff scoping

	def test_staff_see_only_their_teams_sources(self):
		html = str(self._form(self.staff)["sources"])
		self.assertIn("<strong>Linked (1):</strong> Alpha", html)
		self.assertIn("Zeta", html)
		self.assertIn("NoSubj", html)
		for hidden in ("Beta", "Foreign", "Team A2", "Team B"):
			with self.subTest(hidden):
				self.assertNotIn(hidden, html)

	def test_untouched_field_is_unchanged_and_keeps_other_teams_links(self):
		form = self._form(self.staff, {"sources": [self.alpha.pk]})
		self.assertTrue(form.is_valid(), form.errors)
		self.assertNotIn("sources", form.changed_data)
		form.save()
		self.assertEqual(self._linked(), {self.alpha, self.beta, self.foreign})

	def test_staff_unticking_removes_only_their_own_link(self):
		form = self._form(self.staff, {"sources": []})
		self.assertTrue(form.is_valid(), form.errors)
		form.save()
		self.assertEqual(self._linked(), {self.beta, self.foreign})

	def test_staff_cannot_post_another_teams_source(self):
		form = self._form(self.staff, {"sources": [self.alpha.pk, self.foreign.pk]})
		self.assertFalse(form.is_valid())
		self.assertIn("sources", form.errors)

	# Superuser

	def test_superuser_sees_and_can_untick_every_source(self):
		html = str(self._form(self.superuser)["sources"])
		for source in (self.zeta, self.alpha, self.nosubj, self.beta, self.foreign):
			self.assertIn(source.name, html)
		form = self._form(self.superuser, {"sources": [self.alpha.pk, self.beta.pk]})
		self.assertTrue(form.is_valid(), form.errors)
		form.save()
		self.assertEqual(self._linked(), {self.alpha, self.beta})

	# The change page

	def test_change_page_renders_the_widget_without_related_icons(self):
		self.client.force_login(self.superuser)
		response = self.client.get(reverse(self.change_url_name, args=[self.content.pk]))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'class="grouped-sources"')
		self.assertContains(response, "admin/css/grouped_sources.css")
		self.assertContains(response, SOURCES_HELP_TEXT)
		for icon in ("add_id_sources", "change_id_sources", "delete_id_sources", "view_id_sources"):
			with self.subTest(icon):
				self.assertNotContains(response, icon)
		# Teams and subjects keep their select and its Ctrl help text; the
		# sources row must not have it.
		page = response.content.decode()
		row = page[page.index("field-sources"):]
		row = row[: row.index('class="form-row', 1)]
		self.assertIn("grouped-sources", row)
		self.assertNotIn("Hold down", row)


class ArticleSourcesWidgetTests(_SourcesWidgetTests, TestCase):
	admin_class = ArticleAdmin
	content_model = Articles
	source_for = "science paper"
	change_url_name = "admin:gregory_articles_change"

	def make_content(self):
		return Articles.objects.create(title="Srcw article", link="https://example.com/srcw-article")


class TrialSourcesWidgetTests(_SourcesWidgetTests, TestCase):
	admin_class = TrialAdmin
	content_model = Trials
	source_for = "trials"
	change_url_name = "admin:gregory_trials_change"

	def make_content(self):
		return Trials.objects.create(title="Srcw trial", link="https://example.com/srcw-trial")


class PatentSourcesUnchangedTests(TestCase):
	def test_patents_keep_filter_horizontal(self):
		self.assertIn("sources", PatentAdmin.filter_horizontal)
		superuser = User.objects.create_superuser("srcw-pat-root", "p@example.com", "pw")
		request = RequestFactory().get("/")
		request.user = superuser
		model_admin = PatentAdmin(Patents, admin.site)
		patent = Patents.objects.create(family_id="srcw-1", title="Srcw patent")
		form = model_admin.get_form(request, patent)(instance=patent)
		self.assertNotIsInstance(
			getattr(form.fields["sources"].widget, "widget", form.fields["sources"].widget),
			GroupedSourceCheckboxSelectMultiple,
		)
