"""
History on Sources: who added or changed a source, and through which door.

The fetch anchor (last_successful_fetch_at) is the importers' bookkeeping, so it
is left out of the history and the importers write it with a queryset update.
The importers' own success-path tests assert that no history row is written;
this file covers the model, the editor stamp and the admin history page.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from organizations.models import Organization

from gregory.editor_history import editing_as
from gregory.models import PublicationType, Sources, Team

User = get_user_model()


class SourcesHistoryTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Src History Org", slug="src-history-org")
		self.team = Team.objects.create(organization=self.org, name="T", slug="src-history-team")
		self.user = User.objects.create_user(
			"src-editor", "src-editor@example.com", "x", first_name="Ana", last_name="Editor"
		)

	def _source(self, **kwargs):
		return Sources.objects.create(
			name="PubMed | MS", source_for="science paper", method="pubmed_api",
			team=self.team, pubmed_query="multiple sclerosis", **kwargs,
		)

	def test_create_writes_a_history_row_naming_the_editor_and_the_door(self):
		with editing_as(self.user, "mcp"):
			source = self._source()
		row = source.history.get()
		self.assertEqual(row.history_type, "+")
		self.assertEqual(row.editor_user, self.user)
		self.assertEqual(row.editor_label, "Ana Editor <src-editor@example.com>")
		self.assertEqual(row.via, "mcp")

	def test_an_edit_writes_a_history_row(self):
		source = self._source()
		source.name = "PubMed | Multiple sclerosis"
		source.save()
		rows = list(source.history.order_by("history_id"))
		self.assertEqual([r.history_type for r in rows], ["+", "~"])
		self.assertEqual(rows[1].name, "PubMed | Multiple sclerosis")

	def test_the_fetch_anchor_is_not_recorded(self):
		self.assertFalse(hasattr(Sources.history.model, "last_successful_fetch_at"))

	def test_a_queryset_update_of_the_anchor_writes_no_row(self):
		source = self._source()
		before = source.history.count()
		Sources.objects.filter(pk=source.pk).update(last_successful_fetch_at=timezone.now())
		self.assertEqual(source.history.count(), before)

	def test_excluded_publication_types_are_tracked(self):
		source = self._source()
		review = PublicationType.objects.create(name="Review", ui="D016454")
		source.excluded_publication_types.add(review)
		latest = source.history.latest("history_date", "history_id")
		self.assertEqual(
			[t.publicationtype_id for t in latest.excluded_publication_types.all()],
			[review.pk],
		)


class SourceAdminHistoryTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Src Admin Org", slug="src-admin-org")
		self.other_org = Organization.objects.create(name="Src Other Org", slug="src-other-org")
		self.team = Team.objects.create(organization=self.org, name="T", slug="src-admin-team")
		self.other_team = Team.objects.create(
			organization=self.other_org, name="O", slug="src-other-team"
		)
		self.own = Sources.objects.create(name="Own", source_for="science paper", team=self.team)
		self.foreign = Sources.objects.create(
			name="Foreign", source_for="science paper", team=self.other_team
		)
		self.superuser = User.objects.create_superuser("src-root", "root@example.com", "x")
		self.staff = User.objects.create_user("src-staff", "staff@example.com", "x", is_staff=True)
		self.org.add_user(self.staff)
		self.staff.user_permissions.add(
			*Permission.objects.filter(
				content_type__app_label="gregory", content_type__model="sources"
			)
		)

	def _history_url(self, source):
		return reverse("admin:gregory_sources_history", args=[source.pk])

	def test_staff_can_open_the_history_of_their_organisations_source(self):
		self.client.force_login(self.staff)
		resp = self.client.get(self._history_url(self.own))
		self.assertEqual(resp.status_code, 200)

	def test_staff_cannot_open_the_history_of_another_organisations_source(self):
		self.client.force_login(self.staff)
		# simple-history would fall back to the historical copy of a row outside the
		# user's queryset; SourceAdmin.history_view refuses it instead.
		resp = self.client.get(self._history_url(self.foreign))
		self.assertEqual(resp.status_code, 404)

	def test_a_malformed_id_is_a_404_not_a_server_error(self):
		self.client.force_login(self.staff)
		resp = self.client.get(reverse("admin:gregory_sources_history", args=["not-a-number"]))
		self.assertEqual(resp.status_code, 404)

	def test_superuser_can_open_any_sources_history(self):
		self.client.force_login(self.superuser)
		self.assertEqual(self.client.get(self._history_url(self.foreign)).status_code, 200)
