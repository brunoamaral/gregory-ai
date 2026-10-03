"""
Attribution (D11): history rows on ArticleSiteContent, ArticleSubjectRelevance
and ArticleTrialReference record the named person and the door they came
through (EditorHistoryMixin, stamped by stamp_editor_on_history).
"""

import json
from datetime import timedelta
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import TestCase
from django.urls import reverse
from django.utils.functional import SimpleLazyObject
from django.utils.timezone import now
from organizations.models import Organization
from simple_history.models import HistoricalRecords

from api.models import APIAccessScheme
from gregory.editor_history import editing_as, editor_label
from gregory.models import (
	Articles,
	ArticleSiteContent,
	ArticleSubjectRelevance,
	ArticleTrialReference,
	OrganizationSite,
	Subject,
	Team,
	Trials,
)

User = get_user_model()


def _request(**attrs):
	"""What simple-history's middleware stashes, reduced to what the stamp reads."""
	return SimpleNamespace(**attrs)


class HistoryRequestScope:
	"""Run a block as if simple-history's middleware had stashed ``request``."""

	def __init__(self, request):
		self.request = request

	def __enter__(self):
		HistoricalRecords.context.request = self.request

	def __exit__(self, *exc):
		if hasattr(HistoricalRecords.context, "request"):
			del HistoricalRecords.context.request


class EditorHistoryFixture:
	def setUp(self):
		self.org = Organization.objects.create(name="History Org", slug="history-org")
		self.team = Team.objects.create(organization=self.org, name="T", slug="history-team")
		self.subject = Subject.objects.create(
			team=self.team, subject_name="History Subject", subject_slug="history-subject"
		)
		self.site = Site.objects.create(domain="history.test.example.com", name="History")
		OrganizationSite.objects.create(organization=self.org, site=self.site, is_default=True)
		self.article = Articles.objects.create(title="History article", link="https://h.test/1")
		self.user = User.objects.create_user(
			"editor",
			"editor@example.com",
			"x",
			first_name="Ana",
			last_name="Editor",
		)


class EditingAsScopeTest(EditorHistoryFixture, TestCase):
	def test_site_content_history_names_the_person_and_door(self):
		with editing_as(self.user, "mcp"):
			ArticleSiteContent.objects.create(
				article=self.article, site=self.site, takeaways="By hand"
			)

		row = ArticleSiteContent.history.get()
		self.assertEqual(row.editor_user, self.user)
		self.assertEqual(row.editor_label, "Ana Editor <editor@example.com>")
		self.assertEqual(row.via, "mcp")

	def test_relevance_history_names_the_person(self):
		with editing_as(self.user, "mcp"):
			ArticleSubjectRelevance.objects.create(
				article=self.article, subject=self.subject, is_relevant=True
			)

		row = ArticleSubjectRelevance.history.get()
		self.assertEqual(row.editor_user, self.user)
		self.assertEqual(row.via, "mcp")
		self.assertIs(row.is_relevant, True)

	def test_trial_link_history_covers_create_and_delete(self):
		trial = Trials.objects.create(title="Linked trial", link="https://t.test/1")
		with editing_as(self.user, "mcp"):
			ref = ArticleTrialReference.objects.create(
				article=self.article,
				trial=trial,
				identifier_type="manual",
				identifier_value="NCT00000000",
				source="manual",
				created_by=self.user,
			)
			ref.delete()

		rows = list(ArticleTrialReference.history.order_by("history_date"))
		self.assertEqual([r.history_type for r in rows], ["+", "-"])
		for row in rows:
			self.assertEqual(row.editor_user, self.user)
			self.assertEqual(row.via, "mcp")

	def test_scope_ends_with_the_block(self):
		with editing_as(self.user, "mcp"):
			pass
		ArticleSiteContent.objects.create(
			article=self.article, site=self.site, takeaways="After"
		)

		row = ArticleSiteContent.history.get()
		self.assertIsNone(row.editor_user)
		self.assertEqual(row.editor_label, "")
		self.assertEqual(row.via, "")

	def test_label_survives_deleting_the_user(self):
		with editing_as(self.user, "mcp"):
			ArticleSiteContent.objects.create(
				article=self.article, site=self.site, takeaways="x"
			)
		self.user.delete()

		row = ArticleSiteContent.history.get()
		self.assertIsNone(row.editor_user)
		self.assertEqual(row.editor_label, "Ana Editor <editor@example.com>")
		self.assertEqual(row.via, "mcp")

	def test_label_without_email_or_name(self):
		bare = User.objects.create_user("bare-user", "", "x")
		self.assertEqual(editor_label(bare), "bare-user")


class RequestDerivedEditorTest(EditorHistoryFixture, TestCase):
	def test_admin_request_is_attributed_to_the_user_via_admin(self):
		request = _request(
			user=self.user, resolver_match=SimpleNamespace(namespaces=["admin"])
		)
		with HistoryRequestScope(request):
			ArticleSiteContent.objects.create(
				article=self.article, site=self.site, takeaways="From admin"
			)

		row = ArticleSiteContent.history.get()
		self.assertEqual(row.editor_user, self.user)
		self.assertEqual(row.via, "admin")

	def test_request_can_name_its_own_door(self):
		request = _request(user=self.user, editor_via="mcp", resolver_match=None)
		with HistoryRequestScope(request):
			ArticleSubjectRelevance.objects.create(
				article=self.article, subject=self.subject, is_relevant=False
			)

		self.assertEqual(ArticleSubjectRelevance.history.get().via, "mcp")

	def test_api_key_request_records_the_door_not_a_person(self):
		scheme = SimpleNamespace(client_name="a key")
		request = _request(
			api_access_scheme=SimpleLazyObject(lambda: scheme), user=self.user
		)
		with HistoryRequestScope(request):
			ArticleSiteContent.objects.create(
				article=self.article, site=self.site, takeaways="By key"
			)

		row = ArticleSiteContent.history.get()
		self.assertEqual(row.via, "api_key")
		self.assertIsNone(row.editor_user)

	def test_lazy_none_api_key_does_not_count_as_a_key(self):
		request = _request(
			api_access_scheme=SimpleLazyObject(lambda: None),
			user=self.user,
			resolver_match=SimpleNamespace(namespaces=["admin"]),
		)
		with HistoryRequestScope(request):
			ArticleSiteContent.objects.create(
				article=self.article, site=self.site, takeaways="x"
			)

		self.assertEqual(ArticleSiteContent.history.get().via, "admin")

	def test_anonymous_request_leaves_the_row_blank(self):
		request = _request(user=SimpleNamespace(is_authenticated=False))
		with HistoryRequestScope(request):
			ArticleSiteContent.objects.create(
				article=self.article, site=self.site, takeaways="x"
			)

		row = ArticleSiteContent.history.get()
		self.assertIsNone(row.editor_user)
		self.assertEqual(row.via, "")

	def test_a_change_with_no_request_is_blank(self):
		ArticleSubjectRelevance.objects.create(
			article=self.article, subject=self.subject, is_relevant=True
		)

		row = ArticleSubjectRelevance.history.get()
		self.assertIsNone(row.editor_user)
		self.assertEqual(row.editor_label, "")
		self.assertEqual(row.via, "")

	def test_explicit_scope_wins_over_the_request(self):
		other = User.objects.create_user("other", "other@example.com", "x")
		request = _request(user=other, editor_via="admin", resolver_match=None)
		with HistoryRequestScope(request), editing_as(self.user, "mcp"):
			ArticleSiteContent.objects.create(
				article=self.article, site=self.site, takeaways="x"
			)

		row = ArticleSiteContent.history.get()
		self.assertEqual(row.editor_user, self.user)
		self.assertEqual(row.via, "mcp")


class EndToEndAttributionTest(EditorHistoryFixture, TestCase):
	def test_edit_article_with_an_api_key_records_via_api_key(self):
		team = self.team
		self.article.doi = "10.7777/history"
		self.article.save()
		self.article.teams.add(team)
		scheme = APIAccessScheme.objects.create(
			client_name="history-key",
			client_contacts="k@example.com",
			organization=self.org,
			site=self.site,
			begin_date=now() - timedelta(days=1),
			end_date=now() + timedelta(days=30),
		)

		resp = self.client.post(
			"/articles/edit/",
			data=json.dumps({"doi": "10.7777/history", "takeaways": "Via key"}),
			content_type="application/json",
			HTTP_AUTHORIZATION=scheme.api_key,
		)

		self.assertEqual(resp.status_code, 200)
		row = ArticleSiteContent.history.get()
		self.assertEqual(row.via, "api_key")
		self.assertEqual(row.api_access_scheme, scheme)
		self.assertIsNone(row.editor_user)

	def test_admin_relevance_ajax_records_the_staff_user_via_admin(self):
		staff = User.objects.create_superuser("root", "root@example.com", "x")
		self.client.force_login(staff)
		url = reverse("admin:update_article_relevance")

		resp = self.client.post(
			url,
			data=json.dumps(
				{
					"article_id": self.article.article_id,
					"subject_id": self.subject.pk,
					"action": "mark_relevant",
				}
			),
			content_type="application/json",
		)

		self.assertEqual(resp.status_code, 200, resp.content)
		row = ArticleSubjectRelevance.history.get()
		self.assertEqual(row.editor_user, staff)
		self.assertEqual(row.via, "admin")


class StampFailureTest(EditorHistoryFixture, TestCase):
	def test_a_stamp_failure_keeps_the_save_and_is_logged(self):
		"""A broken stamp must not break the save, and must not vanish either:
		a history row without its editor is a gap in the audit trail."""
		from unittest import mock

		with (
			mock.patch("gregory.editor_history.current_editor", side_effect=RuntimeError("boom")),
			self.assertLogs("gregory.signals", level="ERROR") as logs,
		):
			ArticleSubjectRelevance.objects.create(
				article=self.article, subject=self.subject, is_relevant=True
			)

		row = ArticleSubjectRelevance.history.get(article=self.article)
		self.assertIsNone(row.editor_user)
		self.assertEqual(row.editor_label, "")
		self.assertIn("HistoricalArticleSubjectRelevance", logs.output[0])
