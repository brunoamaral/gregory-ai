"""
Tests for gregory.admin.SourceBulkActionMixin — the "Add source to selected…"
and "Remove source from selected…" bulk admin actions on Articles/Trials.

Run with:
    docker exec gregory python manage.py test gregory.tests.test_admin_source_bulk_actions
"""

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import TestCase, RequestFactory
from organizations.models import Organization, OrganizationUser

from gregory.admin import ArticleAdmin, TrialAdmin
from gregory.models import Articles, Trials, Sources, Team

User = get_user_model()


class SourceBulkActionMixinTests(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.site = AdminSite()

		self.org_a = Organization.objects.create(name="Org A", slug="src-org-a")
		self.org_b = Organization.objects.create(name="Org B", slug="src-org-b")
		self.team_a = Team.objects.create(
			organization=self.org_a, name="Team A", slug="src-team-a"
		)
		self.team_b = Team.objects.create(
			organization=self.org_b, name="Team B", slug="src-team-b"
		)

		self.source_a = Sources.objects.create(
			name="Source A", source_for="science paper", team=self.team_a
		)
		self.source_b = Sources.objects.create(
			name="Source B", source_for="science paper", team=self.team_b
		)
		self.trial_source_a = Sources.objects.create(
			name="Trial Source A", source_for="trials", team=self.team_a
		)

		self.article1 = Articles.objects.create(
			title="Article 1", link="https://example.com/1"
		)
		self.article1.teams.add(self.team_a)
		self.article2 = Articles.objects.create(
			title="Article 2", link="https://example.com/2"
		)
		self.article2.teams.add(self.team_a)

		self.trial1 = Trials.objects.create(title="Trial 1", link="https://example.com/t1")
		self.trial1.teams.add(self.team_a)

		self.staff_user = User.objects.create_user(
			username="staff-a", password="pw", is_staff=True
		)
		OrganizationUser.objects.create(organization=self.org_a, user=self.staff_user)

		self.superuser = User.objects.create_superuser(
			username="root", email="r@e.com", password="pw"
		)

		self.article_admin = ArticleAdmin(Articles, self.site)
		self.trial_admin = TrialAdmin(Trials, self.site)

	def _request(self, user, post_data=None):
		if post_data is not None:
			request = self.factory.post("/admin/gregory/articles/", post_data)
		else:
			request = self.factory.get("/admin/gregory/articles/")
		request.user = user
		request.session = {}
		request._messages = FallbackStorage(request)
		return request

	def test_source_queryset_scoped_by_source_for_and_org_for_staff(self):
		request = self._request(self.staff_user)
		qs = self.article_admin._get_source_queryset(request)
		self.assertIn(self.source_a, qs)
		self.assertNotIn(self.source_b, qs)  # belongs to a different org
		self.assertNotIn(self.trial_source_a, qs)  # wrong source_for for Articles

	def test_source_queryset_unrestricted_by_org_for_superuser(self):
		request = self._request(self.superuser)
		qs = self.article_admin._get_source_queryset(request)
		self.assertIn(self.source_a, qs)
		self.assertIn(self.source_b, qs)
		self.assertNotIn(self.trial_source_a, qs)  # still filtered by source_for

	def test_add_source_action_links_selected_articles(self):
		request = self._request(
			self.superuser, post_data={"apply": "1", "source": self.source_a.pk}
		)
		queryset = Articles.objects.filter(pk__in=[self.article1.pk, self.article2.pk])
		self.article_admin.add_source_action(request, queryset)

		self.assertIn(self.source_a, self.article1.sources.all())
		self.assertIn(self.source_a, self.article2.sources.all())

	def test_remove_source_action_unlinks_selected_articles(self):
		self.article1.sources.add(self.source_a)
		self.article2.sources.add(self.source_a)

		request = self._request(
			self.superuser, post_data={"apply": "1", "source": self.source_a.pk}
		)
		queryset = Articles.objects.filter(pk__in=[self.article1.pk, self.article2.pk])
		self.article_admin.remove_source_action(request, queryset)

		self.assertNotIn(self.source_a, self.article1.sources.all())
		self.assertNotIn(self.source_a, self.article2.sources.all())

	def test_add_source_action_records_history(self):
		request = self._request(
			self.superuser, post_data={"apply": "1", "source": self.source_a.pk}
		)
		queryset = Articles.objects.filter(pk=self.article1.pk)
		self.article_admin.add_source_action(request, queryset)

		self.assertTrue(self.article1.history.filter(history_type="~").exists())

	def test_add_source_action_rejects_source_invalid_for_model(self):
		# trial_source_a is source_for='trials', not valid for Articles, so it's
		# excluded from the form's queryset and validation should fail (no crash,
		# no link created).
		request = self._request(
			self.superuser, post_data={"apply": "1", "source": self.trial_source_a.pk}
		)
		queryset = Articles.objects.filter(pk=self.article1.pk)
		self.article_admin.add_source_action(request, queryset)
		self.article1.refresh_from_db()
		self.assertNotIn(self.trial_source_a, self.article1.sources.all())

	def test_trial_admin_scopes_to_trials_sources(self):
		request = self._request(
			self.superuser, post_data={"apply": "1", "source": self.trial_source_a.pk}
		)
		queryset = Trials.objects.filter(pk=self.trial1.pk)
		self.trial_admin.add_source_action(request, queryset)
		self.assertIn(self.trial_source_a, self.trial1.sources.all())

	def test_confirmation_page_rendered_without_apply(self):
		request = self._request(self.superuser)
		queryset = Articles.objects.filter(pk=self.article1.pk)
		response = self.article_admin.add_source_action(request, queryset)
		self.assertEqual(response.status_code, 200)
