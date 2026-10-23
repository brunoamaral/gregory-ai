"""
The subject-relevance inline on the Articles admin page
(gregory.admin.ArticleSubjectRelevanceInline).

An article can be shared between organisations, and its relevance for each
organisation's subjects belongs to that organisation. A staff member sees and
marks only their own organisations' subjects; another organisation's rows are
hidden from them and left alone when they save.
"""

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase
from organizations.models import Organization, OrganizationUser

from gregory.admin import ArticleSubjectRelevanceInline
from gregory.models import ArticleSubjectRelevance, Articles, Subject, Team

User = get_user_model()


class RelevanceInlineScopeTest(TestCase):
	def setUp(self):
		self.org_a = Organization.objects.create(name="Org A", slug="relinline-org-a")
		self.org_b = Organization.objects.create(name="Org B", slug="relinline-org-b")
		self.team_a = Team.objects.create(organization=self.org_a, name="Team A", slug="relinline-team-a")
		self.team_b = Team.objects.create(organization=self.org_b, name="Team B", slug="relinline-team-b")
		self.subject_a = Subject.objects.create(
			subject_name="Rel A", subject_slug="relinline-a", team=self.team_a, auto_predict=True
		)
		self.subject_b = Subject.objects.create(
			subject_name="Rel B", subject_slug="relinline-b", team=self.team_b, auto_predict=True
		)
		self.article = Articles.objects.create(title="Shared", link="https://example.com/relinline")
		self.article.teams.add(self.team_a, self.team_b)
		self.article.subjects.add(self.subject_a, self.subject_b)
		self.row_a = ArticleSubjectRelevance.objects.create(
			article=self.article, subject=self.subject_a, is_relevant=None
		)
		self.row_b = ArticleSubjectRelevance.objects.create(
			article=self.article, subject=self.subject_b, is_relevant=True
		)

		# Org A staff WITH the permissions: without them the inline skips
		# validation and the forged cases below would pass vacuously.
		self.staff = User.objects.create_user(username="relinline-staff", password="pw", is_staff=True)
		OrganizationUser.objects.create(organization=self.org_a, user=self.staff)
		# Editing is limited to the user's teams (gregory.team_scope).
		self.team_a.members.add(self.staff)
		self.staff.user_permissions.add(
			*Permission.objects.filter(
				content_type__app_label="gregory",
				content_type__model__in=["articles", "articlesubjectrelevance"],
			)
		)
		self.superuser = User.objects.create_superuser("relinline-root", "root@example.com", "pw")
		self.inline = ArticleSubjectRelevanceInline(Articles, admin.site)

	def _request(self, user):
		request = RequestFactory().get("/")
		request.user = user
		return request

	def _submit(self, user, rows):
		"""Bind the formset as the change view would. ``rows`` is a list of
		(pk or None, subject_pk, is_relevant)."""
		request = self._request(user)
		formset_class = self.inline.get_formset(request, self.article)
		prefix = formset_class.get_default_prefix()
		queryset = self.inline.get_queryset(request)
		shown = formset_class(instance=self.article, prefix=prefix, queryset=queryset)
		data = {
			f"{prefix}-TOTAL_FORMS": str(len(rows)),
			f"{prefix}-INITIAL_FORMS": str(len(shown.initial_forms)),
			f"{prefix}-MIN_NUM_FORMS": "0",
			f"{prefix}-MAX_NUM_FORMS": "1000",
		}
		for i, (pk, subject_pk, is_relevant) in enumerate(rows):
			data[f"{prefix}-{i}-id"] = str(pk or "")
			data[f"{prefix}-{i}-article"] = str(self.article.pk)
			data[f"{prefix}-{i}-subject"] = str(subject_pk)
			data[f"{prefix}-{i}-is_relevant"] = str(is_relevant)
		return formset_class(data, instance=self.article, prefix=prefix, queryset=queryset)

	def test_staff_see_only_their_organisations_rows(self):
		rows = self.inline.get_queryset(self._request(self.staff)).filter(article=self.article)
		self.assertEqual(list(rows), [self.row_a])

	def test_superuser_sees_every_row(self):
		rows = self.inline.get_queryset(self._request(self.superuser)).filter(article=self.article)
		self.assertEqual(set(rows), {self.row_a, self.row_b})

	def test_opening_the_page_creates_rows_only_for_own_subjects(self):
		ArticleSubjectRelevance.objects.all().delete()
		self.inline.get_formset(self._request(self.staff), self.article)
		self.assertEqual(
			set(ArticleSubjectRelevance.objects.values_list("subject_id", flat=True)),
			{self.subject_a.pk},
		)

	def test_new_row_for_another_organisations_subject_is_rejected(self):
		# Without the existing row, so the unique (article, subject) check
		# can't be what rejects it.
		self.row_b.delete()
		formset = self._submit(
			self.staff, [(self.row_a.pk, self.subject_a.pk, "None"), (None, self.subject_b.pk, "False")]
		)
		self.assertFalse(formset.is_valid())
		self.assertIn("subject", formset.forms[1].errors)

	def test_existing_row_cannot_be_moved_to_another_subject(self):
		formset = self._submit(self.staff, [(self.row_a.pk, self.subject_b.pk, "True")])
		self.assertTrue(formset.is_valid(), formset.errors)
		formset.save()
		self.row_a.refresh_from_db()
		self.assertEqual(self.row_a.subject, self.subject_a)
		self.assertTrue(self.row_a.is_relevant)

	def test_saving_leaves_another_organisations_row_alone(self):
		# A regression guard: the hidden row isn't in the formset, so saving
		# the page must not touch it.
		formset = self._submit(self.staff, [(self.row_a.pk, self.subject_a.pk, "False")])
		self.assertTrue(formset.is_valid(), formset.errors)
		formset.save()
		self.row_b.refresh_from_db()
		self.assertTrue(self.row_b.is_relevant)
		self.row_a.refresh_from_db()
		self.assertIs(self.row_a.is_relevant, False)
