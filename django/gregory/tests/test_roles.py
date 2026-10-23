"""The Editor role (gregory/roles.py) and the setup_roles command."""

from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from organizations.models import Organization, OrganizationUser

from gregory.models import Articles, Patents, Sources, Subject, Team, TeamCategory, Trials
from gregory.roles import (
	EDITOR_GROUP,
	EDITOR_PERMISSIONS,
	FORBIDDEN_APPS,
	editor_permission_keys,
	sync_editor_group,
)

User = get_user_model()

# Every model the role may add to, change or delete, and what keeps an editor
# inside their own organisation on it. On top of that, every one of them edits
# only within the editor's own teams (TeamEditMixin / TeamEditInlineMixin,
# test_team_edit_rule). Adding a write permission to the role
# means adding the model here, with the scoping that makes it safe, and a test
# for that scoping if one doesn't exist yet.
SCOPED_WRITE_MODELS = {
	"gregory.articles": "OrganizationFilterMixin (source -> team -> org); relations scoped by TeamScopedRelationsFormMixin (test_admin_relation_scope)",
	"gregory.trials": "OrganizationFilterMixin; TeamScopedRelationsFormMixin (test_admin_relation_scope)",
	"gregory.patents": "OrganizationFilterMixin; TeamScopedRelationsFormMixin (test_admin_relation_scope)",
	"gregory.articlesitecontent": "inline scoped to the organisation's sites; standalone admin superuser-write (test_admin_article_site_content)",
	"gregory.patentsitecontent": "as articlesitecontent",
	"gregory.trialorgcontent": "inline scoped by organization FK, follows the trial's team; standalone admin superuser-write (test_team_edit_rule)",
	"gregory.articlesubjectrelevance": "inline scoped to the organisation's subjects (test_admin_relevance_inline_scope)",
	"gregory.articlecategoryassignment": "inline scoped by the category's team (test_admin_category_inline_scope)",
	"gregory.trialcategoryassignment": "as articlecategoryassignment",
	"gregory.patentcategoryassignment": "as articlecategoryassignment",
	"gregory.patenttriallink": "PatentTrialLinkAdmin.get_queryset via patent sources -> team -> org",
	"gregory.teamcategory": "OrganizationFilterMixin via team; team and subjects pickers scoped (EditorInTheAdminTest)",
}

# Shared between organisations, or reference data everyone reads: an editor
# deleting or creating one affects every organisation.
NEVER_ADD_OR_DELETE = {
	"gregory.articles",
	"gregory.trials",
	"gregory.patents",
	"gregory.authors",
	"gregory.sponsor",
	"gregory.entities",
	"gregory.publicationtype",
	"gregory.meshterm",
	"gregory.sources",
	"gregory.subject",
	"gregory.team",
}


class RoleDefinitionTest(TestCase):
	def test_no_forbidden_apps(self):
		apps = {label.split(".")[0] for label in EDITOR_PERMISSIONS}
		self.assertEqual(apps & FORBIDDEN_APPS, set())

	def test_writes_only_on_scoped_models(self):
		writable = {
			label
			for label, actions in EDITOR_PERMISSIONS.items()
			if set(actions) - {"view"}
		}
		self.assertEqual(writable, set(SCOPED_WRITE_MODELS))

	def test_shared_content_is_never_added_or_deleted(self):
		for label in NEVER_ADD_OR_DELETE:
			actions = set(EDITOR_PERMISSIONS.get(label, ()))
			self.assertFalse(actions & {"add", "delete"}, label)

	def test_every_permission_exists(self):
		keys = set(editor_permission_keys())
		found = {
			(app_label, codename)
			for app_label, codename in Permission.objects.values_list(
				"content_type__app_label", "codename"
			)
		}
		self.assertEqual(keys - found, set())


class SyncEditorGroupTest(TestCase):
	def test_creates_the_group_with_exactly_the_role(self):
		group, added, removed = sync_editor_group()
		self.assertEqual(group.name, EDITOR_GROUP)
		self.assertEqual(
			set(group.permissions.values_list("content_type__app_label", "codename")),
			set(editor_permission_keys()),
		)
		self.assertEqual(len(added), len(set(editor_permission_keys())))
		self.assertEqual(removed, [])

	def test_is_idempotent(self):
		sync_editor_group()
		_, added, removed = sync_editor_group()
		self.assertEqual((added, removed), ([], []))

	def test_removes_permissions_added_by_hand(self):
		group, _, _ = sync_editor_group()
		group.permissions.add(Permission.objects.get(codename="change_user"))
		_, _, removed = sync_editor_group()
		self.assertEqual(removed, ["auth.change_user"])
		self.assertFalse(group.permissions.filter(codename="change_user").exists())


class SetupRolesCommandTest(TestCase):
	def run_command(self, *args):
		out = StringIO()
		try:
			call_command("setup_roles", *args, stdout=out)
			code = 0
		except SystemExit as exit:
			code = exit.code
		return code, out.getvalue()

	def test_check_fails_before_the_group_exists_and_writes_nothing(self):
		code, out = self.run_command("--check")
		self.assertEqual(code, 1)
		self.assertIn("missing  gregory.change_articles", out)
		self.assertFalse(Group.objects.filter(name=EDITOR_GROUP).exists())

	def test_sync_then_check_passes(self):
		self.assertEqual(self.run_command()[0], 0)
		code, out = self.run_command("--check")
		self.assertEqual(code, 0)
		self.assertIn("matches the role", out)

	def test_reports_staff_without_a_team_or_with_direct_permissions(self):
		org = Organization.objects.create(name="Org", slug="roles-org")
		team = Team.objects.create(organization=org, name="Team", slug="roles-team")
		on_team = User.objects.create_user(username="on-team", is_staff=True)
		team.members.add(on_team)
		loose = User.objects.create_user(username="loose", is_staff=True)
		loose.user_permissions.add(Permission.objects.get(codename="change_articles"))
		User.objects.create_superuser("roles-root", "r@e.com", "pw")

		_, out = self.run_command("--check")
		no_team = out.split("Not on any team")[1].split("Permissions held directly")[0]
		self.assertIn("loose", no_team)
		self.assertNotIn("on-team", no_team)
		self.assertIn("loose (1)", out)
		self.assertNotIn("roles-root", out)


class EditorInTheAdminTest(TestCase):
	"""An Editor-group member gets the role through the group alone."""

	def setUp(self):
		sync_editor_group()
		self.org_a = Organization.objects.create(name="Org A", slug="roles-org-a")
		self.org_b = Organization.objects.create(name="Org B", slug="roles-org-b")
		team_a = Team.objects.create(organization=self.org_a, name="Team A", slug="roles-team-a")
		team_b = Team.objects.create(organization=self.org_b, name="Team B", slug="roles-team-b")
		source_a = Sources.objects.create(name="Src A", source_for="science paper", team=team_a)
		source_b = Sources.objects.create(name="Src B", source_for="science paper", team=team_b)
		self.article_a = Articles.objects.create(title="Own", link="https://example.com/roles-a")
		self.article_a.sources.add(source_a)
		self.article_b = Articles.objects.create(title="Other", link="https://example.com/roles-b")
		self.article_b.sources.add(source_b)
		self.team_a, self.team_b = team_a, team_b
		self.source_a, self.source_b = source_a, source_b
		self.subject_a = Subject.objects.create(subject_name="Subj A", subject_slug="roles-subj-a", team=team_a)
		self.subject_b = Subject.objects.create(subject_name="Subj B", subject_slug="roles-subj-b", team=team_b)

		self.editor = User.objects.create_user(username="editor", password="pw", is_staff=True)
		OrganizationUser.objects.create(organization=self.org_a, user=self.editor)
		team_a.members.add(self.editor)
		self.editor.groups.add(Group.objects.get(name=EDITOR_GROUP))
		self.client.force_login(self.editor)

	def test_sees_own_organisations_articles_only(self):
		resp = self.client.get(reverse("admin:gregory_articles_changelist"))
		self.assertEqual(resp.status_code, 200)
		shown = set(resp.context["cl"].result_list)
		self.assertIn(self.article_a, shown)
		self.assertNotIn(self.article_b, shown)

	def test_cannot_open_another_organisations_article(self):
		resp = self.client.get(reverse("admin:gregory_articles_change", args=[self.article_b.pk]))
		self.assertEqual(resp.status_code, 302)  # admin redirects a missing object to the index

	def test_cannot_delete_or_add_articles(self):
		delete = self.client.get(reverse("admin:gregory_articles_delete", args=[self.article_a.pk]))
		self.assertEqual(delete.status_code, 403)
		add = self.client.get(reverse("admin:gregory_articles_add"))
		self.assertEqual(add.status_code, 403)

	def test_no_user_or_group_admin(self):
		self.assertEqual(self.client.get(reverse("admin:auth_user_changelist")).status_code, 403)
		self.assertEqual(self.client.get(reverse("admin:auth_group_changelist")).status_code, 403)

	def test_renders_the_article_trial_and_patent_pages_with_their_inlines(self):
		trial_source = Sources.objects.create(name="Trials A", source_for="trials", team=self.team_a)
		trial = Trials.objects.create(title="Trial A", link="https://example.com/roles-trial")
		trial.sources.add(trial_source)
		patent = Patents.objects.create(family_id="roles-1", title="Patent A")
		patent.sources.add(
			Sources.objects.create(name="Patents A", source_for="patents", team=self.team_a)
		)
		pages = {
			"gregory_articles_change": (self.article_a.pk, {"ArticleSiteContent", "ArticleSubjectRelevance", "ArticleCategoryAssignment", "ArticleMeshHeading"}),
			"gregory_trials_change": (trial.pk, {"TrialOrgContent", "TrialCategoryAssignment", "TrialCountry", "TrialSite"}),
			"gregory_patents_change": (patent.pk, {"PatentSiteContent", "PatentCategoryAssignment", "PatentTrialLink", "PatentPublication"}),
		}
		for name, (pk, expected) in pages.items():
			with self.subTest(page=name):
				resp = self.client.get(reverse(f"admin:{name}", args=[pk]))
				self.assertEqual(resp.status_code, 200)
				shown = {f.formset.model.__name__ for f in resp.context["inline_admin_formsets"]}
				self.assertLessEqual(expected, shown)

	def test_history_of_another_organisations_article_is_refused(self):
		self.article_b.title = "Other, edited"
		self.article_b.save()
		version = self.article_b.history.order_by("history_date").first()
		self.assertEqual(
			self.client.get(reverse("admin:gregory_articles_history", args=[self.article_b.pk])).status_code,
			403,
		)
		url = reverse("admin:gregory_articles_simple_history", args=[self.article_b.pk, version.history_id])
		self.assertEqual(self.client.get(url).status_code, 403)

	def test_history_of_another_organisations_source_cannot_be_saved(self):
		self.source_b.name = "Src B renamed"
		self.source_b.save()
		version = self.source_b.history.order_by("history_date").first()
		url = reverse("admin:gregory_sources_simple_history", args=[self.source_b.pk, version.history_id])
		self.assertEqual(self.client.get(url).status_code, 403)
		self.client.post(url, {"name": "Taken", "team": self.team_a.pk, "source_for": "science paper"})
		self.source_b.refresh_from_db()
		self.assertEqual(self.source_b.name, "Src B renamed")
		self.assertEqual(self.source_b.team, self.team_b)

	def test_own_article_history_is_viewable_but_not_revertable(self):
		self.article_a.title = "Own, edited"
		self.article_a.save()
		version = self.article_a.history.order_by("history_date").first()
		url = reverse("admin:gregory_articles_simple_history", args=[self.article_a.pk, version.history_id])
		self.assertEqual(self.client.get(url).status_code, 200)
		self.assertEqual(self.client.post(url, {"title": "Own"}).status_code, 403)
		self.article_a.refresh_from_db()
		self.assertEqual(self.article_a.title, "Own, edited")

	def test_category_form_offers_and_accepts_only_own_organisation(self):
		resp = self.client.get(reverse("admin:gregory_teamcategory_add"))
		self.assertEqual(resp.status_code, 200)
		form = resp.context["adminform"].form
		self.assertEqual(set(form.fields["team"].queryset), {self.team_a})
		self.assertEqual(set(form.fields["subjects"].queryset), {self.subject_a})

		resp = self.client.post(
			reverse("admin:gregory_teamcategory_add"),
			{
				"team": self.team_b.pk,
				"category_name": "Forged",
				"category_slug": "forged",
				"subjects": [self.subject_b.pk],
			},
		)
		self.assertEqual(resp.status_code, 200)  # form redisplayed with errors
		errors = resp.context["adminform"].form.errors
		self.assertIn("team", errors)
		self.assertIn("subjects", errors)
		self.assertFalse(TeamCategory.objects.filter(category_name="Forged").exists())

	def test_author_page_lists_only_own_organisations_articles(self):
		from gregory.models import Authors

		author = Authors.objects.create(given_name="Ada", family_name="Shared")
		self.article_a.authors.add(author)
		self.article_b.authors.add(author)
		resp = self.client.get(reverse("admin:gregory_authors_change", args=[author.pk]))
		self.assertEqual(resp.status_code, 200)
		(inline,) = [
			f for f in resp.context["inline_admin_formsets"] if f.formset.model is Articles.authors.through
		]
		self.assertEqual({form.instance.articles for form in inline.formset.forms}, {self.article_a})
