"""Admin writes need a model permission, not just a staff account.

Covers custom admin actions (Django only filters actions that declare
``permissions``) and the custom admin views that write data.
"""

import json

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase
from django.urls import reverse
from organizations.models import Organization, OrganizationUser

from gregory.admin import PredictionRunLogAdmin, SourceAdmin
from gregory.models import (
	ArticleSubjectRelevance,
	Articles,
	PredictionRunLog,
	Sources,
	Subject,
	Team,
)

User = get_user_model()


def grant(user, *codenames):
	"""Give ``user`` the named gregory permissions (e.g. "view_team")."""
	user.user_permissions.add(
		*Permission.objects.filter(content_type__app_label="gregory", codename__in=codenames)
	)
	# Drop Django's per-instance permission cache.
	for attr in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
		user.__dict__.pop(attr, None)


class EveryActionDeclaresPermissionsTest(TestCase):
	"""Without ``permissions``, an action is offered to every user who can
	view the list, so a view-only user could run it."""

	def test_every_registered_action_declares_permissions(self):
		missing = []
		for model, model_admin in admin.site._registry.items():
			for func, name, _description in model_admin._get_base_actions():
				if not getattr(func, "allowed_permissions", None):
					missing.append(f"{type(model_admin).__name__}.{name}")
		self.assertEqual(missing, [], "Add @admin.action(permissions=[...]) to these")


class OrgFixture(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.org_a = Organization.objects.create(name="Org A", slug="wp-org-a")
		cls.org_b = Organization.objects.create(name="Org B", slug="wp-org-b")
		cls.team_a = Team.objects.create(organization=cls.org_a, name="Team A", slug="wp-team-a")
		cls.team_a2 = Team.objects.create(organization=cls.org_a, name="Team A2", slug="wp-team-a2")
		cls.team_b = Team.objects.create(organization=cls.org_b, name="Team B", slug="wp-team-b")
		cls.team_b2 = Team.objects.create(organization=cls.org_b, name="Team B2", slug="wp-team-b2")
		cls.subject_a = Subject.objects.create(
			subject_name="Subject A", subject_slug="wp-subject-a", team=cls.team_a
		)
		cls.subject_b = Subject.objects.create(
			subject_name="Subject B", subject_slug="wp-subject-b", team=cls.team_b
		)
		cls.source_a = Sources.objects.create(
			name="Source A", source_for="science paper", team=cls.team_a, active=True
		)
		cls.article_a = Articles.objects.create(title="Article A", link="https://example.com/wp-a")
		cls.article_a.subjects.add(cls.subject_a)
		cls.article_b = Articles.objects.create(title="Article B", link="https://example.com/wp-b")
		cls.article_b.subjects.add(cls.subject_b)

	def setUp(self):
		self.staff = User.objects.create_user(username="staff-a", password="pw", is_staff=True)
		OrganizationUser.objects.create(organization=self.org_a, user=self.staff)
		# Editing is limited to the user's teams (gregory.team_scope).
		self.team_a.members.add(self.staff)

	def login(self):
		self.client.force_login(self.staff)


class ViewOnlyUserGetsNoWriteActionsTest(OrgFixture):
	def test_view_only_staff_gets_no_source_actions(self):
		grant(self.staff, "view_sources")
		request = RequestFactory().get("/admin/gregory/sources/")
		request.user = self.staff
		actions = SourceAdmin(Sources, admin.site).get_actions(request)
		self.assertNotIn("activate_sources", actions)
		self.assertNotIn("deactivate_sources", actions)
		self.assertNotIn("reassign_to_team_action", actions)

	def test_forged_action_post_does_not_run(self):
		grant(self.staff, "view_sources")
		self.login()
		self.client.post(
			reverse("admin:gregory_sources_changelist"),
			{"action": "deactivate_sources", "_selected_action": [self.source_a.pk]},
		)
		self.source_a.refresh_from_db()
		self.assertTrue(self.source_a.active)

	def test_staff_with_change_gets_source_actions(self):
		grant(self.staff, "view_sources", "change_sources")
		request = RequestFactory().get("/admin/gregory/sources/")
		request.user = self.staff
		actions = SourceAdmin(Sources, admin.site).get_actions(request)
		self.assertIn("activate_sources", actions)


class PredictionRunLogPermissionsTest(OrgFixture):
	def test_change_and_delete_follow_model_permissions(self):
		grant(self.staff, "view_predictionrunlog")
		request = RequestFactory().get("/")
		request.user = self.staff
		model_admin = PredictionRunLogAdmin(PredictionRunLog, admin.site)
		self.assertFalse(model_admin.has_change_permission(request))
		self.assertFalse(model_admin.has_delete_permission(request))


class TeamReassignViewTest(OrgFixture):
	def url(self, team):
		return reverse("admin:gregory_team_reassign", args=[team.pk])

	def test_another_organisations_team_is_404(self):
		grant(self.staff, "view_team", "change_team")
		self.login()
		self.assertEqual(self.client.get(self.url(self.team_b)).status_code, 404)
		resp = self.client.post(
			self.url(self.team_b), {"target_team": self.team_b2.pk, "conflict": "skip"}
		)
		self.assertEqual(resp.status_code, 404)

	def test_needs_change_permission(self):
		grant(self.staff, "view_team")
		self.login()
		self.assertEqual(self.client.get(self.url(self.team_a)).status_code, 403)

	def test_staff_cannot_reassign_even_their_own_team(self):
		# Team settings and membership are superuser-only (TeamAdmin).
		grant(self.staff, "view_team", "change_team")
		self.login()
		self.assertEqual(self.client.get(self.url(self.team_a)).status_code, 403)

	def test_superuser_can_open_reassign(self):
		root = User.objects.create_superuser("reassign-root", "r@e.com", "pw")
		self.client.force_login(root)
		self.assertEqual(self.client.get(self.url(self.team_a)).status_code, 200)


class MlCoverageViewTest(OrgFixture):
	url = "/admin/gregory/predictionrunlog/ml-coverage/"

	def test_needs_view_permission(self):
		self.login()
		self.assertEqual(self.client.get(reverse("admin:predictionrunlog_ml_coverage")).status_code, 403)

	def test_lists_only_own_organisations_teams(self):
		grant(self.staff, "view_predictionrunlog")
		self.login()
		resp = self.client.get(reverse("admin:predictionrunlog_ml_coverage"))
		self.assertEqual(resp.status_code, 200)
		teams = set(resp.context["teams"])
		self.assertIn(self.team_a, teams)
		self.assertNotIn(self.team_b, teams)


class ReviewStatusViewTest(OrgFixture):
	def url(self, subject):
		return reverse("admin:article_review_status") + f"?subject_id={subject.pk}"

	def test_another_organisations_subject_is_404(self):
		grant(self.staff, "add_articlesubjectrelevance", "change_articlesubjectrelevance")
		self.login()
		self.assertEqual(self.client.get(self.url(self.subject_b)).status_code, 404)
		resp = self.client.post(
			self.url(self.subject_b),
			{"action": "mark_relevant", "selected_articles": [self.article_b.pk]},
		)
		self.assertEqual(resp.status_code, 404)
		self.assertFalse(ArticleSubjectRelevance.objects.exists())

	def test_batch_needs_relevance_permissions(self):
		self.login()
		resp = self.client.post(
			self.url(self.subject_a),
			{"action": "mark_relevant", "selected_articles": [self.article_a.pk]},
		)
		self.assertEqual(resp.status_code, 403)
		self.assertFalse(ArticleSubjectRelevance.objects.exists())

	def test_batch_only_touches_articles_carrying_the_subject(self):
		grant(self.staff, "add_articlesubjectrelevance", "change_articlesubjectrelevance")
		self.login()
		self.client.post(
			self.url(self.subject_a),
			{"action": "mark_relevant", "selected_articles": [self.article_a.pk, self.article_b.pk]},
		)
		rows = ArticleSubjectRelevance.objects.all()
		self.assertEqual(
			list(rows.values_list("article_id", "subject_id", "is_relevant")),
			[(self.article_a.pk, self.subject_a.pk, True)],
		)


class RelevanceAjaxTest(OrgFixture):
	def post(self, article, subject):
		return self.client.post(
			reverse("admin:update_article_relevance"),
			data=json.dumps(
				{"article_id": article.pk, "subject_id": subject.pk, "action": "mark_relevant"}
			),
			content_type="application/json",
		)

	def test_needs_relevance_permissions(self):
		self.login()
		self.assertEqual(self.post(self.article_a, self.subject_a).status_code, 403)
		self.assertFalse(ArticleSubjectRelevance.objects.exists())

	def test_another_organisations_subject_is_404(self):
		grant(self.staff, "add_articlesubjectrelevance", "change_articlesubjectrelevance")
		self.login()
		self.assertEqual(self.post(self.article_b, self.subject_b).status_code, 404)
		self.assertFalse(ArticleSubjectRelevance.objects.exists())

	def test_article_without_the_subject_is_404(self):
		grant(self.staff, "add_articlesubjectrelevance", "change_articlesubjectrelevance")
		self.login()
		self.assertEqual(self.post(self.article_b, self.subject_a).status_code, 404)
		self.assertFalse(ArticleSubjectRelevance.objects.exists())

	def test_own_subject_and_article(self):
		grant(self.staff, "add_articlesubjectrelevance", "change_articlesubjectrelevance")
		self.login()
		self.assertEqual(self.post(self.article_a, self.subject_a).status_code, 200)
		self.assertTrue(
			ArticleSubjectRelevance.objects.get(article=self.article_a, subject=self.subject_a).is_relevant
		)


class SourcesOverviewBulkTest(OrgFixture):
	def test_bulk_post_needs_change_permission(self):
		grant(self.staff, "view_sources")
		self.login()
		resp = self.client.post(
			reverse("admin:sources_overview"),
			{"bulk_action": "deactivate", "selected_sources": [self.source_a.pk]},
		)
		self.assertEqual(resp.status_code, 403)
		self.source_a.refresh_from_db()
		self.assertTrue(self.source_a.active)

	def test_bulk_post_with_change_permission(self):
		grant(self.staff, "view_sources", "change_sources")
		self.login()
		self.client.post(
			reverse("admin:sources_overview"),
			{"bulk_action": "deactivate", "selected_sources": [self.source_a.pk]},
		)
		self.source_a.refresh_from_db()
		self.assertFalse(self.source_a.active)


class OAuthAdminIsSuperuserOnlyTest(TestCase):
	@staticmethod
	def oauth_models():
		from oauth2_provider.models import (
			get_access_token_model,
			get_application_model,
			get_grant_model,
			get_id_token_model,
			get_refresh_token_model,
		)

		return [
			get_application_model(),
			get_access_token_model(),
			get_grant_model(),
			get_id_token_model(),
			get_refresh_token_model(),
		]

	def test_oauth_admins_refuse_staff_with_model_permissions(self):
		staff = User.objects.create_user(username="token-staff", password="pw", is_staff=True)
		models = self.oauth_models()
		staff.user_permissions.add(
			*Permission.objects.filter(
				content_type__app_label__in={m._meta.app_label for m in models}
			)
		)
		request = RequestFactory().get("/")
		request.user = User.objects.get(pk=staff.pk)
		for model in models:
			self.assertTrue(request.user.has_perm(f"{model._meta.app_label}.view_{model._meta.model_name}"))
			model_admin = admin.site.get_model_admin(model)
			with self.subTest(model=model._meta.label):
				self.assertFalse(model_admin.has_module_permission(request))
				self.assertFalse(model_admin.has_view_permission(request))
				self.assertFalse(model_admin.has_add_permission(request))
				self.assertFalse(model_admin.has_change_permission(request))
				self.assertFalse(model_admin.has_delete_permission(request))
				self.assertEqual(model_admin.get_actions(request), {})

	def test_superuser_keeps_revoke(self):
		from oauth2_provider.models import get_access_token_model

		root = User.objects.create_superuser(username="token-root", email="r@e.com", password="pw")
		request = RequestFactory().get("/")
		request.user = root
		model_admin = admin.site.get_model_admin(get_access_token_model())
		self.assertIn("revoke_tokens", model_admin.get_actions(request))


class PredictionRunLogChangelistTest(OrgFixture):
	def test_dashboard_counts_only_own_organisations_runs(self):
		PredictionRunLog.objects.create(team=self.team_a, subject=self.subject_a, run_type="train", success=True)
		PredictionRunLog.objects.create(team=self.team_b, subject=self.subject_b, run_type="train", success=True)
		grant(self.staff, "view_predictionrunlog")
		self.login()
		resp = self.client.get(reverse("admin:gregory_predictionrunlog_changelist"))
		self.assertEqual(resp.status_code, 200)
		self.assertEqual(resp.context["training_success_count"], 1)
		self.assertEqual({run.team for run in resp.context["recent_runs"]}, {self.team_a})


class SponsorMergePermissionTest(TestCase):
	def test_merge_needs_change_and_delete_on_sponsors(self):
		from gregory.admin import SponsorMergeCandidateAdmin
		from gregory.models import SponsorMergeCandidate

		staff = User.objects.create_user(username="merge-staff", password="pw", is_staff=True)
		grant(staff, "view_sponsormergecandidate", "change_sponsormergecandidate")
		request = RequestFactory().get("/")
		request.user = staff
		model_admin = SponsorMergeCandidateAdmin(SponsorMergeCandidate, admin.site)
		actions = model_admin.get_actions(request)
		self.assertIn("dismiss_action", actions)
		self.assertNotIn("merge_action", actions)

		grant(staff, "change_sponsor", "delete_sponsor")
		request.user = User.objects.get(pk=staff.pk)
		self.assertIn("merge_action", model_admin.get_actions(request))


class AnnouncementSendTest(OrgFixture):
	def setUp(self):
		super().setUp()
		from subscriptions.models import Announcement

		self.announcement = Announcement.objects.create(
			subject="Hello", body="<p>body</p>", status="draft", organization=self.org_a
		)
		self.staff.user_permissions.add(Permission.objects.get(codename="view_announcement"))
		self.login()

	def test_view_only_staff_cannot_send_or_send_test(self):
		for name in ("subscriptions_announcement_send", "subscriptions_announcement_send_test"):
			url = reverse(f"admin:{name}", args=[self.announcement.pk])
			with self.subTest(name=name):
				self.assertEqual(self.client.get(url).status_code, 403)
				self.assertEqual(self.client.post(url, {"confirm": "yes"}).status_code, 403)
		self.announcement.refresh_from_db()
		self.assertEqual(self.announcement.status, "draft")

	def test_view_only_staff_get_no_send_buttons(self):
		resp = self.client.get(
			reverse("admin:subscriptions_announcement_change", args=[self.announcement.pk])
		)
		self.assertEqual(resp.status_code, 200)
		self.assertFalse(resp.context["show_send_buttons"])
