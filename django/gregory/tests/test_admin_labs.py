"""The labs admin: superuser-only, every edit manual, the subject ranking, the author and
article pages, and the author actions."""

from django.contrib.admin.sites import site as admin_site
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse
from organizations.models import Organization, OrganizationUser

from gregory.admin import ArticleAdmin, AuthorsAdmin
from gregory.models import (
	ArticleAuthorship,
	Articles,
	Authors,
	Institution,
	Lab,
	LabMembership,
	Subject,
	Team,
)
from gregory.roles import EDITOR_GROUP, editor_permission_keys, sync_editor_group
from gregory.services.lab_grouping import Assignment, LabResult, LabsResult
from gregory.services.labs import apply_labs

User = get_user_model()
MANUAL = LabMembership.Source.MANUAL
COMPUTED = LabMembership.Source.COMPUTED
HEAD, MEMBER, COLLABORATOR = (
	LabMembership.Role.HEAD,
	LabMembership.Role.MEMBER,
	LabMembership.Role.COLLABORATOR,
)


def author(n, openalex_id=None):
	return Authors.objects.create(
		given_name=f"Given{n}", family_name=f"Family{n}", full_name=f"Given{n} Family{n}", openalex_id=openalex_id
	)


def make_lab(head, *, source=COMPUTED, **kwargs):
	lab = Lab.objects.create(head=head, **kwargs)
	LabMembership.objects.create(author=head, lab=lab, role=HEAD, source=source, shared_articles=3)
	return lab


def join(lab, who, role=MEMBER, source=COMPUTED, **kwargs):
	return LabMembership.objects.create(author=who, lab=lab, role=role, source=source, **kwargs)


def _form_data(form):
	data = {}
	for bound in form:
		value = bound.value()
		if value is None or value is False:
			continue
		if value is True:
			value = "true" if type(bound.field.widget).__name__ == "NullBooleanSelect" else "on"
		if isinstance(value, (list, tuple)):
			continue
		data[bound.html_name] = value
	return data


def post_data(response, **overrides):
	"""The data a browser would post back from a change page, read from the context."""
	data = _form_data(response.context["adminform"].form)
	for inline in response.context["inline_admin_formsets"]:
		formset = inline.formset
		data.update(_form_data(formset.management_form))
		for form in formset.forms:
			data.update(_form_data(form))
	data.update(overrides)
	return data


class LabAdminCase(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.superuser = User.objects.create_superuser("root", "root@example.org", "pw")
		cls.staff = User.objects.create_user("staff", "staff@example.org", "pw", is_staff=True)
		cls.org = Organization.objects.create(name="Labs Org", slug="labs-org")
		cls.team = Team.objects.create(organization=cls.org, name="Labs Team", slug="labs-team")
		cls.subject = Subject.objects.create(subject_name="Subj S", subject_slug="subj-s", team=cls.team)
		cls.other_subject = Subject.objects.create(subject_name="Subj T", subject_slug="subj-t", team=cls.team)

	def login(self, user=None):
		self.client.force_login(user or self.superuser)

	def lab_membership(self, who):
		return LabMembership.objects.get(author=who)


class PermissionTests(LabAdminCase):
	def setUp(self):
		self.head = author(1)
		self.lab = make_lab(self.head)
		self.member = author(2)
		join(self.lab, self.member)
		self.institution = Institution.objects.create(openalex_id="I1", name="Inst")

	def _editor(self):
		sync_editor_group()
		editor = User.objects.create_user("editor", "e@example.org", "pw", is_staff=True)
		editor.groups.add(Group.objects.get(name=EDITOR_GROUP))
		OrganizationUser.objects.create(organization=self.org, user=editor)
		self.team.members.add(editor)
		return editor

	def _lab_urls(self):
		return [
			reverse("admin:gregory_lab_changelist"),
			reverse("admin:gregory_lab_add"),
			reverse("admin:gregory_lab_change", args=[self.lab.pk]),
			reverse("admin:gregory_lab_delete", args=[self.lab.pk]),
			reverse("admin:gregory_institution_changelist"),
		]

	def test_staff_and_editor_get_403_on_every_lab_url(self):
		for user in (self.staff, self._editor()):
			self.login(user)
			for url in self._lab_urls():
				self.assertEqual(self.client.get(url).status_code, 403, (user.username, url))
			# Even the actions' posts and the lab autocomplete are refused.
			response = self.client.post(
				reverse("admin:gregory_lab_changelist"),
				{"action": "merge_selected_labs", "_selected_action": [self.lab.pk]},
			)
			self.assertEqual(response.status_code, 403)
			response = self.client.get(
				reverse("admin:autocomplete"),
				{"app_label": "gregory", "model_name": "labmembership", "field_name": "lab", "term": ""},
			)
			self.assertEqual(response.status_code, 403)

	def test_the_admin_index_does_not_list_labs_for_a_staff_user(self):
		self.login(self.staff)
		self.assertNotContains(self.client.get(reverse("admin:index")), "/gregory/lab/")
		self.login()
		self.assertContains(self.client.get(reverse("admin:index")), "/gregory/lab/")

	def test_a_superuser_gets_the_lab_pages(self):
		self.login()
		for url in self._lab_urls():
			self.assertEqual(self.client.get(url).status_code, 200, url)

	def test_the_author_pages_show_no_lab_parts_to_an_editor(self):
		editor = self._editor()
		self.login(editor)
		changelist = self.client.get(reverse("admin:gregory_authors_changelist"))
		self.assertEqual(changelist.status_code, 200)
		for text in ("lab_display", "has_lab", "openalex_duplicate", "assign_selected_authors_to_lab"):
			self.assertNotContains(changelist, text)
		change = self.client.get(reverse("admin:gregory_authors_change", args=[self.member.pk]))
		self.assertEqual(change.status_code, 200)
		self.assertNotContains(change, "lab_membership")
		request = RequestFactory().get("/")
		request.user = editor
		model_admin = AuthorsAdmin(Authors, admin_site)
		self.assertNotIn("lab_display", model_admin.get_list_display(request))
		self.assertNotIn("assign_selected_authors_to_lab", model_admin.get_actions(request))
		self.assertNotIn("remove_selected_authors_from_lab", model_admin.get_actions(request))
		self.assertEqual(len(model_admin.get_list_filter(request)), 2)

	def test_a_forged_author_action_post_by_an_editor_writes_nothing(self):
		self.login(self._editor())
		self.client.post(
			reverse("admin:gregory_authors_changelist"),
			{"action": "remove_selected_authors_from_lab", "_selected_action": [self.member.pk]},
		)
		membership = self.lab_membership(self.member)
		self.assertEqual((membership.lab, membership.source), (self.lab, COMPUTED))

	def test_the_author_page_shows_the_lab_parts_to_a_superuser(self):
		self.login()
		change = self.client.get(reverse("admin:gregory_authors_change", args=[self.member.pk]))
		self.assertContains(change, "lab_membership")
		changelist = self.client.get(reverse("admin:gregory_authors_changelist"))
		for text in ("has_lab", "openalex_duplicate", "assign_selected_authors_to_lab"):
			self.assertContains(changelist, text)

	def test_the_article_page_shows_no_lab_field_to_staff(self):
		article = Articles.objects.create(title="A paper", link="https://example.com/p1")
		request = RequestFactory().get("/")
		model_admin = ArticleAdmin(Articles, admin_site)
		request.user = self.staff
		names = [f for _, opts in model_admin.get_fieldsets(request, article) for f in opts["fields"]]
		self.assertNotIn("lab_line", names)
		request.user = self.superuser
		names = [f for _, opts in model_admin.get_fieldsets(request, article) for f in opts["fields"]]
		self.assertIn("lab_line", names)

	def test_setup_roles_grants_nothing_on_the_lab_models(self):
		sync_editor_group()
		forbidden = {"lab", "labmembership", "historicallab", "historicallabmembership", "institution", "articleauthorship"}
		role_models = {codename.split("_", 1)[1] for _, codename in editor_permission_keys()}
		self.assertFalse(role_models & forbidden)
		group = Group.objects.get(name=EDITOR_GROUP)
		held = set(group.permissions.values_list("codename", flat=True))
		self.assertFalse({c for c in held if c.split("_", 1)[1] in forbidden})
		self.assertFalse(
			Permission.objects.filter(group=group, content_type__model__in=forbidden).exists()
		)


class LabListTests(LabAdminCase):
	def _lab_with_people(self, n):
		head = author(n * 10)
		institution = Institution.objects.create(openalex_id=f"I{n}", name=f"Inst {n}", country_code="GB")
		lab = make_lab(head, institution=institution, member_count=2, core_count=1)
		join(lab, author(n * 10 + 1), same_institution=True)
		join(lab, author(n * 10 + 2), same_institution=False)
		return lab

	def _queries(self, **params):
		with CaptureQueriesContext(connection) as ctx:
			response = self.client.get(reverse("admin:gregory_lab_changelist"), params)
		self.assertEqual(response.status_code, 200)
		return len(ctx)

	def test_the_list_runs_a_fixed_number_of_queries(self):
		self.login()
		self._lab_with_people(1)
		self._queries()  # warm the content type and permission caches
		one = self._queries()
		one_ranked = self._queries(subject=self.subject.pk)
		for n in range(2, 31):
			self._lab_with_people(n)
		self.assertEqual(Lab.objects.count(), 30)
		self.assertEqual(self._queries(), one)
		self.assertEqual(self._queries(subject=self.subject.pk), one_ranked)

	def test_columns_show_core_and_external(self):
		self.login()
		self._lab_with_people(1)
		self.assertContains(self.client.get(reverse("admin:gregory_lab_changelist")), "1 / 1")

	def test_filters_and_search(self):
		self.login()
		first = self._lab_with_people(1)
		second = self._lab_with_people(2)
		Lab.objects.filter(pk=second.pk).update(needs_review=True, member_count=20)
		url = reverse("admin:gregory_lab_changelist")
		self.assertEqual([l.pk for l in self.client.get(url, {"needs_review__exact": 1}).context["cl"].result_list], [second.pk])
		self.assertEqual([l.pk for l in self.client.get(url, {"size": "16+"}).context["cl"].result_list], [second.pk])
		self.assertEqual([l.pk for l in self.client.get(url, {"size": "2-5"}).context["cl"].result_list], [first.pk])
		self.assertEqual(self.client.get(url, {"country": "GB"}).context["cl"].result_count, 2)
		self.assertEqual(self.client.get(url, {"country": "FR"}).context["cl"].result_count, 0)
		self.assertEqual([l.pk for l in self.client.get(url, {"q": "Inst 2"}).context["cl"].result_list], [second.pk])
		self.assertEqual([l.pk for l in self.client.get(url, {"q": "Family10"}).context["cl"].result_list], [first.pk])


class SubjectRankingTests(LabAdminCase):
	def setUp(self):
		self.hA, self.mA, self.cA = author(1), author(2), author(3)
		self.hB, self.mB = author(4), author(5)
		self.labA = make_lab(self.hA)
		join(self.labA, self.mA)
		join(self.labA, self.cA, role=COLLABORATOR)
		self.labB = make_lab(self.hB)
		join(self.labB, self.mB)

		def article(n, authors, subject, relevant):
			a = Articles.objects.create(title=f"Paper {n}", link=f"https://example.com/rank-{n}")
			a.subjects.add(subject)
			a.authors.add(*authors)
			Articles.objects.filter(pk=a.pk).update(relevant=relevant)
			return a

		# Lab A: one article with two of its people on it (counted once).
		article(1, [self.hA, self.mA], self.subject, True)
		# A collaborator's article doesn't count.
		article(2, [self.cA], self.subject, True)
		# Lab B: three articles in the subject, two relevant.
		article(3, [self.mB], self.subject, True)
		article(4, [self.hB], self.subject, False)
		article(5, [self.hB, self.mB], self.subject, True)
		# Another subject doesn't count for this one.
		article(6, [self.hA], self.other_subject, True)

	def test_counts_and_order(self):
		self.login()
		url = reverse("admin:gregory_lab_changelist")
		labs = list(self.client.get(url, {"subject": self.subject.pk}).context["cl"].result_list)
		self.assertEqual([lab.pk for lab in labs], [self.labB.pk, self.labA.pk])
		self.assertEqual(
			[(l.articles_in_subject, l.relevant_in_subject) for l in labs], [(3, 2), (1, 1)]
		)
		other = list(self.client.get(url, {"subject": self.other_subject.pk}).context["cl"].result_list)
		self.assertEqual(
			{l.pk: (l.articles_in_subject, l.relevant_in_subject) for l in other},
			{self.labA.pk: (1, 1), self.labB.pk: (0, 0)},
		)

	def test_the_two_columns_only_appear_with_a_subject(self):
		self.login()
		url = reverse("admin:gregory_lab_changelist")
		self.assertNotContains(self.client.get(url), "Relevant in subject")
		self.assertContains(self.client.get(url, {"subject": self.subject.pk}), "Relevant in subject")

	def test_a_bad_subject_value_is_ignored(self):
		self.login()
		response = self.client.get(reverse("admin:gregory_lab_changelist"), {"subject": "abc"})
		self.assertEqual(response.status_code, 200)


class LabFormTests(LabAdminCase):
	def setUp(self):
		self.head, self.new_head = author(1), author(2)
		self.lab = make_lab(self.head, name="Old name")
		self.login()

	def _post_change(self, **overrides):
		url = reverse("admin:gregory_lab_change", args=[self.lab.pk])
		response = self.client.get(url)
		self.assertEqual(response.status_code, 200)
		return self.client.post(url, post_data(response, **overrides))

	def test_editing_the_notes_alone_leaves_the_lab_computed(self):
		self.assertEqual(self._post_change(notes="Seen it").status_code, 302)
		self.lab.refresh_from_db()
		self.assertEqual((self.lab.notes, self.lab.is_manual), ("Seen it", False))

	def test_changing_the_name_marks_the_lab_manual(self):
		self.assertEqual(self._post_change(name="New name").status_code, 302)
		self.lab.refresh_from_db()
		self.assertEqual((self.lab.name, self.lab.is_manual), ("New name", True))

	def test_changing_the_institution_marks_the_lab_manual(self):
		institution = Institution.objects.create(openalex_id="I9", name="Somewhere")
		self._post_change(institution=institution.pk)
		self.lab.refresh_from_db()
		self.assertEqual((self.lab.institution, self.lab.is_manual), (institution, True))

	def test_changing_the_head_rewrites_the_two_memberships(self):
		spare = join(self.lab, self.new_head, source=COMPUTED, shared_articles=5)
		spare.lab = None
		spare.source = MANUAL
		spare.save()
		self.assertEqual(self._post_change(head=self.new_head.pk).status_code, 302)
		self.lab.refresh_from_db()
		self.assertEqual((self.lab.head, self.lab.is_manual), (self.new_head, True))
		new = self.lab_membership(self.new_head)
		self.assertEqual((new.lab, new.role, new.source), (self.lab, HEAD, MANUAL))
		old = self.lab_membership(self.head)
		self.assertEqual((old.lab, old.role, old.source), (self.lab, MEMBER, MANUAL))

	def test_the_new_head_may_come_from_another_lab(self):
		other = make_lab(author(3))
		moved = join(other, self.new_head)
		self._post_change(head=self.new_head.pk)
		moved.refresh_from_db()
		self.assertEqual((moved.lab, moved.role, moved.source), (self.lab, HEAD, MANUAL))

	def test_adding_a_lab_creates_the_heads_manual_membership(self):
		response = self.client.post(
			reverse("admin:gregory_lab_add"),
			{"name": "Brand new", "head": self.new_head.pk, "institution": "", "notes": ""},
		)
		self.assertEqual(response.status_code, 302)
		lab = Lab.objects.get(name="Brand new")
		self.assertTrue(lab.is_manual)
		membership = self.lab_membership(self.new_head)
		self.assertEqual((membership.lab, membership.role, membership.source), (lab, HEAD, MANUAL))

	def test_adding_a_lab_needs_a_head(self):
		response = self.client.post(reverse("admin:gregory_lab_add"), {"name": "Headless"})
		self.assertEqual(response.status_code, 200)
		self.assertFalse(Lab.objects.filter(name="Headless").exists())

	def test_adding_a_lab_whose_head_already_heads_one_fails_with_the_message(self):
		response = self.client.post(
			reverse("admin:gregory_lab_add"), {"name": "Second", "head": self.head.pk}
		)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "already heads")
		self.assertContains(response, "Old name")
		self.assertFalse(Lab.objects.filter(name="Second").exists())

	def test_changing_the_head_to_someone_who_heads_another_lab_fails(self):
		other = make_lab(self.new_head)
		response = self._post_change(head=self.new_head.pk)
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "already heads")
		self.lab.refresh_from_db()
		self.assertEqual(self.lab.head, self.head)
		self.assertEqual(other.head, self.new_head)


class MemberInlineTests(LabAdminCase):
	def setUp(self):
		self.head = author(1)
		self.lab = make_lab(self.head)
		self.other_lab = make_lab(author(2))
		self.login()

	def _post_inline(self, extra_forms, **overrides):
		"""Post the lab page with ``extra_forms`` new member rows appended."""
		url = reverse("admin:gregory_lab_change", args=[self.lab.pk])
		response = self.client.get(url)
		data = post_data(response)
		initial = int(data["memberships-INITIAL_FORMS"])
		data["memberships-TOTAL_FORMS"] = initial + len(extra_forms)
		for i, fields in enumerate(extra_forms, start=initial):
			for key, value in fields.items():
				data[f"memberships-{i}-{key}"] = value
		data.update(overrides)
		return self.client.post(url, data)

	def test_adding_an_author_creates_a_manual_member(self):
		newcomer = author(3)
		self.assertEqual(self._post_inline([{"author": newcomer.pk}]).status_code, 302)
		membership = self.lab_membership(newcomer)
		self.assertEqual((membership.lab, membership.role, membership.source), (self.lab, MEMBER, MANUAL))

	def test_adding_an_author_who_has_a_membership_elsewhere_moves_them(self):
		mover = author(3)
		join(self.other_lab, mover, shared_articles=4)
		self.assertEqual(self._post_inline([{"author": mover.pk}]).status_code, 302)
		membership = self.lab_membership(mover)
		self.assertEqual((membership.lab, membership.role, membership.source), (self.lab, MEMBER, MANUAL))
		self.assertEqual(LabMembership.objects.filter(author=mover).count(), 1)

	def test_a_lab_head_cannot_be_added_as_a_member(self):
		response = self._post_inline([{"author": self.other_lab.head_id}])
		self.assertEqual(response.status_code, 200)
		self.assertEqual(self.lab_membership(self.other_lab.head).role, HEAD)

	def test_changing_an_existing_row_marks_it_manual(self):
		member = author(3)
		membership = join(self.lab, member)
		url = reverse("admin:gregory_lab_change", args=[self.lab.pk])
		data = post_data(self.client.get(url))
		data["memberships-0-same_institution"] = "true"
		self.assertEqual(self.client.post(url, data).status_code, 302)
		membership.refresh_from_db()
		self.assertEqual((membership.same_institution, membership.source), (True, MANUAL))

	def test_a_rebuild_leaves_an_inline_edit_alone(self):
		newcomer = author(3)
		self._post_inline([{"author": newcomer.pk}])
		# The rebuild would put the newcomer in the other lab.
		rebuilt = LabsResult()
		for lab in (self.lab, self.other_lab):
			rebuilt.labs[lab.head_id] = LabResult(lab.head_id, [lab.head_id], None, 3)
			rebuilt.assignments[lab.head_id] = Assignment(lab.head_id, lab.head_id, "head", None, 3, 2025)
		rebuilt.assignments[newcomer.pk] = Assignment(
			newcomer.pk, self.other_lab.head_id, "member", True, 2, 2024
		)
		apply_labs(rebuilt)
		membership = self.lab_membership(newcomer)
		self.assertEqual((membership.lab, membership.source), (self.lab, MANUAL))


class AuthorPageTests(LabAdminCase):
	def setUp(self):
		self.head = author(1)
		self.lab = make_lab(self.head)
		self.other_lab = make_lab(author(2))
		self.person = author(3)
		self.login()

	def _post_author(self, who, **overrides):
		url = reverse("admin:gregory_authors_change", args=[who.pk])
		response = self.client.get(url)
		self.assertEqual(response.status_code, 200)
		return self.client.post(url, post_data(response, **overrides))

	def _rebuild_would_place(self, who, lab):
		rebuilt = LabsResult()
		rebuilt.labs[lab.head_id] = LabResult(lab.head_id, [lab.head_id], None, 3)
		rebuilt.assignments[lab.head_id] = Assignment(lab.head_id, lab.head_id, "head", None, 3, 2025)
		rebuilt.assignments[who.pk] = Assignment(who.pk, lab.head_id, "member", True, 2, 2024)
		apply_labs(rebuilt, force=True)

	def test_choosing_a_lab_creates_a_manual_membership_a_rebuild_leaves_alone(self):
		response = self._post_author(self.person, **{"lab_membership-0-lab": self.lab.pk, "lab_membership-0-role": MEMBER})
		self.assertEqual(response.status_code, 302)
		membership = self.lab_membership(self.person)
		self.assertEqual((membership.lab, membership.role, membership.source), (self.lab, MEMBER, MANUAL))
		self._rebuild_would_place(self.person, self.other_lab)
		membership.refresh_from_db()
		self.assertEqual((membership.lab, membership.source), (self.lab, MANUAL))

	def test_an_empty_lab_with_a_role_pins_the_author_out_of_any_lab(self):
		join(self.lab, self.person)
		url = reverse("admin:gregory_authors_change", args=[self.person.pk])
		data = post_data(self.client.get(url), **{"lab_membership-0-lab": ""})
		self.assertEqual(self.client.post(url, data).status_code, 302)
		membership = self.lab_membership(self.person)
		self.assertEqual((membership.lab, membership.source), (None, MANUAL))
		self._rebuild_would_place(self.person, self.other_lab)
		membership.refresh_from_db()
		self.assertEqual((membership.lab, membership.source), (None, MANUAL))

	def test_a_new_pin_is_a_role_with_no_lab(self):
		response = self._post_author(self.person, **{"lab_membership-0-lab": "", "lab_membership-0-role": MEMBER})
		self.assertEqual(response.status_code, 302)
		membership = self.lab_membership(self.person)
		self.assertEqual((membership.lab, membership.source), (None, MANUAL))

	def test_an_untouched_inline_adds_nothing(self):
		self.assertEqual(self._post_author(self.person).status_code, 302)
		self.assertFalse(LabMembership.objects.filter(author=self.person).exists())

	def test_deleting_the_row_resets_to_computed(self):
		join(self.lab, self.person, source=MANUAL)
		url = reverse("admin:gregory_authors_change", args=[self.person.pk])
		data = post_data(self.client.get(url), **{"lab_membership-0-DELETE": "on"})
		self.assertEqual(self.client.post(url, data).status_code, 302)
		self.assertFalse(LabMembership.objects.filter(author=self.person).exists())

	def test_a_heads_row_cannot_be_changed_or_deleted_here(self):
		url = reverse("admin:gregory_authors_change", args=[self.head.pk])
		data = post_data(self.client.get(url), **{"lab_membership-0-DELETE": "on"})
		self.assertEqual(self.client.post(url, data).status_code, 200)
		self.assertEqual(self.lab_membership(self.head).role, HEAD)
		data = post_data(self.client.get(url), **{"lab_membership-0-lab": self.other_lab.pk})
		self.assertEqual(self.client.post(url, data).status_code, 200)
		self.assertEqual(self.lab_membership(self.head).lab, self.lab)

	def test_the_head_role_is_not_offered_to_a_member(self):
		response = self.client.get(reverse("admin:gregory_authors_change", args=[self.person.pk]))
		form = response.context["inline_admin_formsets"][-1].formset.forms[0]
		self.assertNotIn(HEAD, [value for value, _ in form.fields["role"].choices])


class AuthorListTests(LabAdminCase):
	def setUp(self):
		self.lab = make_lab(author(1))
		self.in_lab = author(2)
		join(self.lab, self.in_lab)
		self.pinned = author(3)
		LabMembership.objects.create(author=self.pinned, lab=None, source=MANUAL)
		self.loose = author(4)
		self.login()
		self.url = reverse("admin:gregory_authors_changelist")

	def _ids(self, **params):
		return {a.pk for a in self.client.get(self.url, params).context["cl"].result_list}

	def test_the_lab_column(self):
		response = self.client.get(self.url)
		self.assertContains(response, "Given1 Family1 lab (member)")
		self.assertContains(response, "No lab (pinned)")

	def test_has_a_lab_filter(self):
		self.assertEqual(self._ids(has_lab="yes"), {self.lab.head_id, self.in_lab.pk})
		self.assertEqual(self._ids(has_lab="pinned"), {self.pinned.pk})
		self.assertEqual(self._ids(has_lab="no"), {self.loose.pk})

	def test_the_list_query_count_does_not_grow_with_rows(self):
		def count():
			with CaptureQueriesContext(connection) as ctx:
				self.client.get(self.url)
			return len(ctx)

		count()  # warm the caches
		before = count()
		for n in range(10, 25):
			join(self.lab, author(n))
		self.assertEqual(count(), before)

	def test_probable_duplicate_filter(self):
		a, b = author(10, "A111"), author(11, "A111")
		alone = author(12, "A222")
		author(13)
		author(14)
		self.assertEqual(self._ids(openalex_duplicate="yes"), {a.pk, b.pk})
		self.assertNotIn(alone.pk, self._ids(openalex_duplicate="yes"))

	def test_probable_duplicate_filter_ignores_empty_ids(self):
		author(10, "")
		author(11, "")
		self.assertEqual(self._ids(openalex_duplicate="yes"), set())

	def test_assign_action(self):
		a, b = author(10), author(11)
		join(self.lab, b, role=COLLABORATOR)
		other = make_lab(author(12))
		data = {"action": "assign_selected_authors_to_lab", "_selected_action": [a.pk, b.pk, self.lab.head_id]}
		page = self.client.post(self.url, data)
		self.assertContains(page, "Assign selected authors to a lab")
		self.assertFalse(LabMembership.objects.filter(author=a).exists())
		response = self.client.post(
			self.url, {**data, "apply": "1", "lab": other.pk, "role": COLLABORATOR}, follow=True
		)
		self.assertEqual(response.status_code, 200)
		for who in (a, b):
			membership = self.lab_membership(who)
			self.assertEqual((membership.lab, membership.role, membership.source), (other, COLLABORATOR, MANUAL))
		head = self.lab_membership(self.lab.head)
		self.assertEqual((head.lab, head.role), (self.lab, HEAD))
		self.assertContains(response, "Skipped 1 lab head")

	def test_remove_action_pins_the_authors(self):
		data = {
			"action": "remove_selected_authors_from_lab",
			"_selected_action": [self.in_lab.pk, self.loose.pk, self.lab.head_id],
		}
		self.client.post(self.url, data, follow=True)
		for who in (self.in_lab, self.loose):
			membership = self.lab_membership(who)
			self.assertEqual((membership.lab, membership.source), (None, MANUAL))
		self.assertEqual(self.lab_membership(self.lab.head).lab, self.lab)


class LabPageBlockTests(LabAdminCase):
	def setUp(self):
		self.institution = Institution.objects.create(openalex_id="I1", name="Inst")
		self.head = author(1, "A100")
		self.twin = author(2, "A100")
		self.lab = make_lab(self.head, institution=self.institution)
		join(self.lab, self.twin, shared_articles=3)
		self.collab = author(3)
		join(self.lab, self.collab, role=COLLABORATOR, shared_articles=1, last_shared_year=2020)
		self.articles = []
		for n in range(12):
			article = Articles.objects.create(
				title=f"Head paper {n}", link=f"https://example.com/hp-{n}", published_date=f"2020-01-{n + 1:02d}T00:00:00Z"
			)
			article.authors.add(self.head)
			ArticleAuthorship.objects.create(
				article=article,
				author=self.head,
				position="last",
				author_index=1,
				raw_affiliation="Dept of Things, Inst\nSecond line" if n < 8 else "Other place",
			)
			self.articles.append(article)
		self.login()
		self.response = self.client.get(reverse("admin:gregory_lab_change", args=[self.lab.pk]))

	def test_the_page_renders(self):
		self.assertEqual(self.response.status_code, 200)

	def test_the_ten_latest_papers(self):
		self.assertContains(self.response, "Head paper 11")
		self.assertContains(self.response, "Head paper 2")
		self.assertNotContains(self.response, "Head paper 1<")
		self.assertNotContains(self.response, "Head paper 0<")

	def test_the_three_most_common_affiliations(self):
		self.assertContains(self.response, "Dept of Things, Inst")
		self.assertContains(self.response, "Second line")
		self.assertContains(self.response, "Other place")

	def test_probable_duplicates_link_to_the_authors_search(self):
		self.assertContains(self.response, f"q={self.head.pk}%2C{self.twin.pk}")

	def test_collaborators_are_listed_read_only(self):
		self.assertContains(self.response, "Given3 Family3")
		self.assertContains(self.response, "2020")

	def test_the_member_inline_lists_the_member_not_the_head_or_collaborator(self):
		inline = self.response.context["inline_admin_formsets"][0].formset
		self.assertEqual([f.instance.author_id for f in inline.forms], [self.twin.pk])


class ArticleLabLineTests(LabAdminCase):
	def setUp(self):
		self.head = author(1)
		self.lab = make_lab(self.head, name="Neuro lab")
		self.first = author(2)
		self.article = Articles.objects.create(title="A paper", link="https://example.com/ll-1")
		self.article.authors.add(self.head, self.first)
		ArticleAuthorship.objects.create(article=self.article, author=self.first, position="first", author_index=0)
		ArticleAuthorship.objects.create(article=self.article, author=self.head, position="last", author_index=1)
		self.model_admin = ArticleAdmin(Articles, admin_site)

	def test_the_line_links_the_last_authors_lab(self):
		html = self.model_admin.lab_line(self.article)
		self.assertIn("Neuro lab", html)
		self.assertIn(reverse("admin:gregory_lab_change", args=[self.lab.pk]), html)

	def test_the_line_is_a_dash_without_a_lab(self):
		self.lab.delete()
		self.assertEqual(self.model_admin.lab_line(self.article), "-")
		self.assertEqual(self.model_admin.lab_line(Articles()), "-")

	def test_the_line_costs_one_query(self):
		with CaptureQueriesContext(connection) as ctx:
			self.model_admin.lab_line(self.article)
		self.assertEqual(len(ctx), 1)

	def test_the_page_shows_the_line_and_the_list_does_not_touch_labs(self):
		self.login()
		page = self.client.get(reverse("admin:gregory_articles_change", args=[self.article.pk]))
		self.assertContains(page, "Neuro lab")
		with CaptureQueriesContext(connection) as ctx:
			self.client.get(reverse("admin:gregory_articles_changelist"))
		self.assertFalse([q for q in ctx.captured_queries if "lab_memberships" in q["sql"]])


class MergeActionTests(LabAdminCase):
	def setUp(self):
		self.keep = make_lab(author(1), name="Keep")
		self.gone = make_lab(author(2), name="Gone")
		self.member = author(3)
		join(self.gone, self.member)
		self.login()
		self.url = reverse("admin:gregory_lab_changelist")
		self.data = {"action": "merge_selected_labs", "_selected_action": [self.keep.pk, self.gone.pk]}

	def test_the_confirmation_page_writes_nothing(self):
		response = self.client.post(self.url, self.data)
		self.assertContains(response, "Merge labs")
		self.assertEqual(Lab.objects.count(), 2)

	def test_merging_end_to_end(self):
		response = self.client.post(self.url, {**self.data, "apply": "1", "keep_lab": self.keep.pk}, follow=True)
		self.assertContains(response, "membership(s) moved")
		self.assertEqual(list(Lab.objects.values_list("pk", flat=True)), [self.keep.pk])
		self.keep.refresh_from_db()
		self.assertTrue(self.keep.is_manual)
		moved = self.lab_membership(self.member)
		self.assertEqual((moved.lab, moved.source), (self.keep, MANUAL))
		old_head = self.lab_membership(Authors.objects.get(given_name="Given2"))
		self.assertEqual((old_head.lab, old_head.role), (self.keep, MEMBER))

	def test_one_lab_is_not_enough(self):
		response = self.client.post(
			self.url, {"action": "merge_selected_labs", "_selected_action": [self.keep.pk]}, follow=True
		)
		self.assertContains(response, "Select at least two labs")
		self.assertEqual(Lab.objects.count(), 2)

	def test_no_survivor_chosen_merges_nothing(self):
		self.client.post(self.url, {**self.data, "apply": "1"}, follow=True)
		self.assertEqual(Lab.objects.count(), 2)
