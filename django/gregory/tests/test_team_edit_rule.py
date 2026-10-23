"""Staff see their organisation's rows but edit only their own teams'.

See gregory/team_scope.py for what "belongs to a team" means per model, and
TeamEditMixin / TeamEditInlineMixin in gregory/admin.py for how the admin
applies it.
"""

import json

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from organizations.models import Organization, OrganizationUser

from gregory.models import (
	ArticleSubjectRelevance,
	Articles,
	Sources,
	Subject,
	Team,
	TeamCategory,
	TrialOrgContent,
	Trials,
)
from gregory.roles import EDITOR_GROUP, sync_editor_group
from gregory.team_scope import can_edit, team_editable, user_team_ids

User = get_user_model()


def grant(user, *codenames):
	user.user_permissions.add(
		*Permission.objects.filter(content_type__app_label="gregory", codename__in=codenames)
	)
	for attr in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
		user.__dict__.pop(attr, None)


class TeamFixture(TestCase):
	"""Org A has teams A1 and A2; the editor is on A1 only. Org B is elsewhere."""

	@classmethod
	def setUpTestData(cls):
		sync_editor_group()
		cls.org_a = Organization.objects.create(name="Org A", slug="ter-org-a")
		cls.org_b = Organization.objects.create(name="Org B", slug="ter-org-b")
		cls.a1 = Team.objects.create(organization=cls.org_a, name="A1", slug="ter-a1")
		cls.a2 = Team.objects.create(organization=cls.org_a, name="A2", slug="ter-a2")
		cls.b = Team.objects.create(organization=cls.org_b, name="B", slug="ter-b")
		cls.src_a1 = Sources.objects.create(name="Src A1", source_for="science paper", team=cls.a1, active=True)
		cls.src_a2 = Sources.objects.create(name="Src A2", source_for="science paper", team=cls.a2, active=True)
		cls.subj_a1 = Subject.objects.create(subject_name="S A1", subject_slug="ter-s-a1", team=cls.a1, auto_predict=True)
		cls.subj_a2 = Subject.objects.create(subject_name="S A2", subject_slug="ter-s-a2", team=cls.a2, auto_predict=True)

		# Owned by A1 through its source; through a tag; and A2's alone.
		cls.by_source = Articles.objects.create(title="By source", link="https://example.com/ter-1")
		cls.by_source.sources.add(cls.src_a1)
		cls.by_tag = Articles.objects.create(title="By tag", link="https://example.com/ter-2")
		cls.by_tag.sources.add(cls.src_a2)
		cls.by_tag.teams.add(cls.a1)
		cls.a2_only = Articles.objects.create(title="A2 only", link="https://example.com/ter-3")
		cls.a2_only.sources.add(cls.src_a2)
		cls.a2_only.teams.add(cls.a2)
		cls.a2_only.subjects.add(cls.subj_a2)

	def setUp(self):
		self.editor = User.objects.create_user(username="ter-editor", password="pw", is_staff=True)
		OrganizationUser.objects.create(organization=self.org_a, user=self.editor)
		self.a1.members.add(self.editor)
		self.editor.groups.add(Group.objects.get(name=EDITOR_GROUP))
		self.client.force_login(self.editor)


class TeamScopeTest(TeamFixture):
	def test_content_belongs_to_a_team_through_a_source_or_a_tag(self):
		self.assertEqual(
			set(team_editable(self.editor, Articles.objects.all())), {self.by_source, self.by_tag}
		)

	def test_team_fk_models(self):
		self.assertEqual(set(team_editable(self.editor, Sources.objects.all())), {self.src_a1})
		self.assertEqual(
			set(team_editable(self.editor, Subject.objects.all())), {self.subj_a1}
		)

	def test_inactive_teams_and_no_team_edit_nothing(self):
		loose = User.objects.create_user(username="ter-loose", is_staff=True)
		self.assertFalse(team_editable(loose, Articles.objects.all()).exists())
		self.a1.is_active = False
		self.a1.save()
		editor = User.objects.get(pk=self.editor.pk)  # team ids are cached per user object
		self.assertEqual(user_team_ids(editor), [])
		self.assertFalse(can_edit(editor, self.by_source))

	def test_superuser_edits_everything(self):
		root = User.objects.create_superuser("ter-root", "r@e.com", "pw")
		self.assertTrue(can_edit(root, self.a2_only))


class ArticlePageTest(TeamFixture):
	def change_url(self, article):
		return reverse("admin:gregory_articles_change", args=[article.pk])

	def test_own_teams_article_is_editable(self):
		resp = self.client.get(self.change_url(self.by_tag))
		self.assertEqual(resp.status_code, 200)
		self.assertTrue(resp.context["has_change_permission"])

	def test_other_teams_article_is_visible_read_only(self):
		resp = self.client.get(self.change_url(self.a2_only))
		self.assertEqual(resp.status_code, 200)
		self.assertFalse(resp.context["has_change_permission"])

	def test_team_scoped_inlines_follow_the_parent(self):
		# Asked of the inlines directly: Django's own change view blanks the
		# inlines of a read-only parent anyway, which would hide a gap here.
		from django.test import RequestFactory

		from gregory.admin import (
			ArticleCategoryAssignmentInline,
			ArticleSiteContentInline,
			ArticleSubjectRelevanceInline,
		)

		request = RequestFactory().get("/")
		request.user = User.objects.get(pk=self.editor.pk)
		for inline_class in (
			ArticleCategoryAssignmentInline,
			ArticleSiteContentInline,
			ArticleSubjectRelevanceInline,
		):
			inline = inline_class(Articles, admin.site)
			with self.subTest(inline=inline_class.__name__):
				self.assertTrue(inline.has_change_permission(request, self.by_source))
				self.assertFalse(inline.has_add_permission(request, self.a2_only))
				self.assertFalse(inline.has_change_permission(request, self.a2_only))
				self.assertFalse(inline.has_delete_permission(request, self.a2_only))

	def test_saving_another_teams_article_is_refused(self):
		resp = self.client.post(self.change_url(self.a2_only), {"title": "Hijacked"})
		self.assertEqual(resp.status_code, 403)
		self.a2_only.refresh_from_db()
		self.assertEqual(self.a2_only.title, "A2 only")

	def test_opening_another_teams_article_creates_no_relevance_rows(self):
		self.client.get(self.change_url(self.a2_only))
		self.assertFalse(ArticleSubjectRelevance.objects.filter(article=self.a2_only).exists())

	def test_delete_follows_the_team(self):
		grant(self.editor, "delete_articles")
		url = reverse("admin:gregory_articles_delete", args=[self.a2_only.pk])
		self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 403)
		self.assertTrue(Articles.objects.filter(pk=self.a2_only.pk).exists())

	def test_bulk_action_skips_other_teams_rows(self):
		resp = self.client.post(
			reverse("admin:gregory_articles_changelist"),
			{
				"action": "add_source_action",
				"_selected_action": [self.by_source.pk, self.a2_only.pk],
				"apply": "1",
				"source": self.src_a1.pk,
			},
			follow=True,
		)
		self.assertTrue(self.by_source.sources.filter(pk=self.src_a1.pk).exists())
		self.assertFalse(self.a2_only.sources.filter(pk=self.src_a1.pk).exists())
		messages = [str(m) for m in resp.context["messages"]]
		self.assertTrue(any("belong to another team" in m for m in messages), messages)

	def test_relation_pickers_offer_only_own_teams(self):
		resp = self.client.get(self.change_url(self.by_tag))
		form = resp.context["adminform"].form
		self.assertEqual(set(form.fields["teams"].queryset), {self.a1})
		self.assertEqual(set(form.fields["subjects"].queryset), {self.subj_a1})
		self.assertEqual(set(form.fields["sources"].queryset), {self.src_a1})


class ReviewPageTest(TeamFixture):
	def test_other_teams_subject_is_visible_but_not_markable(self):
		url = reverse("admin:article_review_status") + f"?subject_id={self.subj_a2.pk}"
		self.assertEqual(self.client.get(url).status_code, 200)
		resp = self.client.post(url, {"action": "mark_relevant", "selected_articles": [self.a2_only.pk]})
		self.assertEqual(resp.status_code, 403)
		resp = self.client.post(
			reverse("admin:update_article_relevance"),
			data=json.dumps({"article_id": self.a2_only.pk, "subject_id": self.subj_a2.pk, "action": "mark_relevant"}),
			content_type="application/json",
		)
		self.assertEqual(resp.status_code, 404)
		self.assertFalse(ArticleSubjectRelevance.objects.exists())


class TeamCategoryTest(TeamFixture):
	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		cls.cat_a1 = TeamCategory.objects.create(team=cls.a1, category_name="Cat A1", category_terms=["x"])
		cls.cat_a2 = TeamCategory.objects.create(team=cls.a2, category_name="Cat A2", category_terms=["y"])

	def test_other_teams_category_cannot_be_saved(self):
		url = reverse("admin:gregory_teamcategory_change", args=[self.cat_a2.pk])
		self.assertEqual(self.client.get(url).status_code, 200)
		self.assertEqual(self.client.post(url, {"category_name": "Taken"}).status_code, 403)

	def test_changelist_edit_of_other_teams_row_is_ignored(self):
		self.client.post(
			reverse("admin:gregory_teamcategory_changelist"),
			{
				"form-TOTAL_FORMS": "2",
				"form-INITIAL_FORMS": "2",
				"form-MIN_NUM_FORMS": "0",
				"form-MAX_NUM_FORMS": "1000",
				"form-0-id": str(self.cat_a1.pk),
				"form-0-modality": "other",
				"form-1-id": str(self.cat_a2.pk),
				"form-1-modality": "other",
				"_save": "Save",
			},
		)
		self.cat_a1.refresh_from_db()
		self.cat_a2.refresh_from_db()
		self.assertEqual(self.cat_a1.modality, "other")
		self.assertIsNone(self.cat_a2.modality)


class SourcesAndTeamsTest(TeamFixture):
	"""Staff with change permissions beyond the Editor role still keep to their team."""

	def setUp(self):
		super().setUp()
		grant(self.editor, "change_sources", "change_team", "view_team")

	def test_source_actions_skip_other_teams_sources(self):
		self.client.post(
			reverse("admin:gregory_sources_changelist"),
			{"action": "deactivate_sources", "_selected_action": [self.src_a1.pk, self.src_a2.pk]},
		)
		self.src_a1.refresh_from_db()
		self.src_a2.refresh_from_db()
		self.assertFalse(self.src_a1.active)
		self.assertTrue(self.src_a2.active)

	def test_reassign_offers_only_own_teams(self):
		resp = self.client.post(
			reverse("admin:gregory_sources_changelist"),
			{"action": "reassign_to_team_action", "_selected_action": [self.src_a1.pk]},
		)
		self.assertEqual(set(resp.context["form"].fields["target_team"].queryset), {self.a1})

	def test_sources_overview_bulk_skips_other_teams_sources(self):
		self.client.post(
			reverse("admin:sources_overview"),
			{"bulk_action": "deactivate", "selected_sources": [self.src_a1.pk, self.src_a2.pk]},
		)
		self.src_a1.refresh_from_db()
		self.src_a2.refresh_from_db()
		self.assertFalse(self.src_a1.active)
		self.assertTrue(self.src_a2.active)

	def test_team_settings_are_superuser_only(self):
		url = reverse("admin:gregory_team_change", args=[self.a1.pk])
		resp = self.client.get(url)
		self.assertEqual(resp.status_code, 200)
		self.assertFalse(resp.context["has_change_permission"])
		self.assertEqual(self.client.post(url, {"team_name": "Renamed"}).status_code, 403)


class TrialOrgContentTest(TeamFixture):
	def test_standalone_page_is_superuser_write(self):
		trial = Trials.objects.create(title="A1 trial", link="https://example.com/ter-trial-a1")
		trial.teams.add(self.a1)
		resp = self.client.post(
			reverse("admin:gregory_trialorgcontent_add"),
			{"trial": trial.pk, "organization": self.org_a.pk, "takeaways": "New"},
		)
		self.assertEqual(resp.status_code, 403)
		self.assertFalse(TrialOrgContent.objects.exists())

	def test_standalone_page_refuses_another_teams_row(self):
		trial = Trials.objects.create(title="A2 trial", link="https://example.com/ter-trial")
		trial.sources.add(Sources.objects.create(name="Trials A2", source_for="trials", team=self.a2))
		content = TrialOrgContent.objects.create(trial=trial, organization=self.org_a, takeaways="Theirs")
		url = reverse("admin:gregory_trialorgcontent_change", args=[content.pk])
		self.assertEqual(self.client.post(url, {"takeaways": "Mine"}).status_code, 403)
		content.refresh_from_db()
		self.assertEqual(content.takeaways, "Theirs")


class AdminRegistryTest(TestCase):
	def test_every_content_admin_applies_the_team_rule(self):
		from gregory.admin import TeamEditMixin

		from gregory.models import Patents, PatentTrialLink, PredictionRunLog

		for model in (Articles, Trials, Patents, Sources, Subject, TeamCategory, PatentTrialLink, PredictionRunLog):
			with self.subTest(model=model.__name__):
				self.assertIsInstance(admin.site.get_model_admin(model), TeamEditMixin)


class SharedFieldsTest(TeamFixture):
	"""Articles and trials are shared: staff edit their own layer, superusers the record."""

	def test_staff_edit_only_teams_subjects_and_sources_on_their_article(self):
		resp = self.client.get(reverse("admin:gregory_articles_change", args=[self.by_source.pk]))
		self.assertEqual(resp.status_code, 200)
		form_fields = set(resp.context["adminform"].form.fields)
		self.assertLessEqual({"teams", "subjects", "sources"}, form_fields)
		self.assertFalse(form_fields & {"title", "summary", "doi", "authors", "published_date", "link"})

	def test_trial_registry_fields_are_read_only_for_staff(self):
		from gregory.admin import TrialAdmin
		from django.test import RequestFactory

		trial = Trials.objects.create(title="Own trial", link="https://example.com/ter-own-trial")
		trial.teams.add(self.a1)
		request = RequestFactory().get("/")
		request.user = self.editor
		readonly = TrialAdmin(Trials, admin.site).get_readonly_fields(request, trial)
		self.assertIn("title", readonly)
		self.assertIn("primary_sponsor", readonly)
		self.assertNotIn("teams", readonly)
		self.assertNotIn("subjects", readonly)

	def test_superuser_edits_the_record(self):
		root = User.objects.create_superuser("ter-root2", "r@e.com", "pw")
		self.client.force_login(root)
		resp = self.client.get(reverse("admin:gregory_articles_change", args=[self.by_source.pk]))
		self.assertIn("title", resp.context["adminform"].form.fields)

	def test_staff_can_still_add_an_article(self):
		from gregory.admin import ArticleAdmin
		from django.test import RequestFactory

		request = RequestFactory().get("/")
		request.user = self.editor
		self.assertNotIn("title", ArticleAdmin(Articles, admin.site).get_readonly_fields(request, None))

	def test_crossref_refresh_is_superuser_only(self):
		self.by_source.doi = "10.1/ter"
		self.by_source.save()
		url = reverse("admin:gregory_articles_crossref_refresh", args=[self.by_source.pk])
		self.assertEqual(self.client.get(url).status_code, 403)
		resp = self.client.get(reverse("admin:gregory_articles_change", args=[self.by_source.pk]))
		self.assertNotContains(resp, url)


def _resubmit_data(resp):
	"""The POST data a browser would send back for an admin change page as
	rendered: the main form's values and every inline formset's."""

	def put(data, form, name, key):
		value = form[name].value()
		if value is None:
			return
		if isinstance(value, (list, tuple)):
			data[key] = [str(v.pk if hasattr(v, "pk") else v) for v in value]
		elif value is True:
			data[key] = "on"
		elif value is False:
			return
		else:
			data[key] = str(value.pk if hasattr(value, "pk") else value)

	data = {}
	form = resp.context["adminform"].form
	for name in form.fields:
		put(data, form, name, name)
	for inline in resp.context["inline_admin_formsets"]:
		formset = inline.formset
		management = formset.management_form
		for name in management.fields:
			data[management.add_prefix(name)] = str(management[name].value())
		for inline_form in formset.forms:
			for name in inline_form.fields:
				put(data, inline_form, name, inline_form.add_prefix(name))
	return data


class EditorSavesTheirArticleTest(TeamFixture):
	def test_editor_saves_their_own_article_through_the_change_page(self):
		self.by_source.teams.add(self.a1)
		url = reverse("admin:gregory_articles_change", args=[self.by_source.pk])
		data = _resubmit_data(self.client.get(url))
		data["subjects"] = [str(self.subj_a1.pk)]
		data["teams"] = [str(self.a1.pk)]
		data["title"] = "Changed by an editor"  # read-only for staff: ignored
		resp = self.client.post(url, data)
		self.assertEqual(resp.status_code, 302, getattr(resp, "context", None) and [
			resp.context["adminform"].form.errors,
			[f.formset.errors for f in resp.context["inline_admin_formsets"]],
		])
		self.by_source.refresh_from_db()
		self.assertEqual(self.by_source.title, "By source")
		self.assertEqual(set(self.by_source.subjects.all()), {self.subj_a1})  # the save happened


class MoreWritePathsTest(TeamFixture):
	def setUp(self):
		super().setUp()
		self.editor = User.objects.get(pk=self.editor.pk)

	def test_forged_relations_of_another_team_are_rejected_and_theirs_kept(self):
		from django.forms import modelform_factory
		from django.test import RequestFactory

		from gregory.admin import ArticleAdmin

		request = RequestFactory().get("/")
		request.user = self.editor
		form_class = modelform_factory(
			Articles, form=ArticleAdmin(Articles, admin.site).form, fields=["teams", "subjects", "sources"]
		)
		forged = form_class(
			{"teams": [self.a2.pk], "subjects": [self.subj_a2.pk], "sources": [self.src_a2.pk]},
			instance=self.by_source,
			request=request,
		)
		self.assertFalse(forged.is_valid())
		self.assertEqual(set(forged.errors), {"teams", "subjects", "sources"})

		# by_tag carries A2's source: hidden from the picker, kept on save.
		form = form_class(
			{"teams": [self.a1.pk], "subjects": [self.subj_a1.pk], "sources": []},
			instance=self.by_tag,
			request=request,
		)
		self.assertTrue(form.is_valid(), form.errors)
		form.save()
		self.assertEqual(set(self.by_tag.sources.all()), {self.src_a2})

	def test_select_across_runs_only_on_own_rows(self):
		grant(self.editor, "change_sources")
		resp = self.client.post(
			reverse("admin:gregory_sources_changelist"),
			{
				"action": "deactivate_sources",
				"_selected_action": [self.src_a1.pk],
				"select_across": "1",
			},
			follow=True,
		)
		self.src_a1.refresh_from_db()
		self.src_a2.refresh_from_db()
		self.assertFalse(self.src_a1.active)
		self.assertTrue(self.src_a2.active)
		self.assertTrue(any("belong to another team" in str(m) for m in resp.context["messages"]))

	def test_delete_selected_deletes_only_own_rows(self):
		grant(self.editor, "delete_sources")
		url = reverse("admin:gregory_sources_changelist")
		data = {"action": "delete_selected", "_selected_action": [self.src_a1.pk, self.src_a2.pk]}
		confirm = self.client.post(url, data)
		self.assertEqual(confirm.status_code, 200)
		self.assertEqual(set(confirm.context["queryset"]), {self.src_a1})
		self.client.post(url, {**data, "post": "yes"})
		self.assertFalse(Sources.objects.filter(pk=self.src_a1.pk).exists())
		self.assertTrue(Sources.objects.filter(pk=self.src_a2.pk).exists())

	def test_reassign_apply_refuses_another_team(self):
		grant(self.editor, "change_sources")
		self.client.post(
			reverse("admin:gregory_sources_changelist"),
			{
				"action": "reassign_to_team_action",
				"_selected_action": [self.src_a1.pk],
				"apply": "1",
				"target_team": self.a2.pk,
			},
		)
		self.src_a1.refresh_from_db()
		self.assertEqual(self.src_a1.team, self.a1)

	def test_patent_and_patent_trial_link_follow_the_team(self):
		from gregory.models import Patents, PatentTrialLink, Sponsor

		patent = Patents.objects.create(family_id="ter-a2", title="A2 patent")
		patent.sources.add(Sources.objects.create(name="Patents A2", source_for="patents", team=self.a2))
		trial = Trials.objects.create(title="T", link="https://example.com/ter-link-trial")
		link = PatentTrialLink.objects.create(
			patent=patent, trial=trial, sponsor=Sponsor.objects.create(name="Sp", slug="ter-sp"),
			timing="during", basis="subject",
		)
		grant(self.editor, "change_patenttriallink")
		resp = self.client.post(reverse("admin:gregory_patents_change", args=[patent.pk]), {})
		self.assertEqual(resp.status_code, 403)
		self.client.post(
			reverse("admin:gregory_patenttriallink_changelist"),
			{"action": "suppress_links", "_selected_action": [link.pk]},
		)
		link.refresh_from_db()
		self.assertFalse(link.suppressed)

	def test_site_content_history_cannot_be_reverted_by_staff(self):
		from django.contrib.sites.models import Site

		from gregory.models import ArticleSiteContent, OrganizationSite

		site = Site.objects.create(domain="ter.example.com", name="Ter")
		OrganizationSite.objects.create(organization=self.org_a, site=site)
		row = ArticleSiteContent.objects.create(article=self.by_source, site=site, takeaways="v1")
		row.takeaways = "v2"
		row.save()
		version = row.history.order_by("history_date").first()
		url = reverse("admin:gregory_articlesitecontent_simple_history", args=[row.pk, version.history_id])
		resp = self.client.post(url, {"article": self.a2_only.pk, "site": site.pk, "takeaways": "forged"})
		self.assertEqual(resp.status_code, 403)
		row.refresh_from_db()
		self.assertEqual((row.article, row.takeaways), (self.by_source, "v2"))

	def test_deleting_another_teams_subject_touches_no_source(self):
		grant(self.editor, "delete_subject", "delete_sources")
		self.src_a2.subject = self.subj_a2
		self.src_a2.save()
		url = reverse("admin:gregory_subject_delete", args=[self.subj_a2.pk])
		resp = self.client.post(url, {"post": "yes", "delete_orphaned_sources": "yes"})
		self.assertEqual(resp.status_code, 403)
		self.assertTrue(Sources.objects.filter(pk=self.src_a2.pk).exists())
		self.assertTrue(Subject.objects.filter(pk=self.subj_a2.pk).exists())

	def test_review_page_hides_buttons_for_another_teams_subject(self):
		own = self.client.get(reverse("admin:article_review_status") + f"?subject_id={self.subj_a1.pk}")
		other = self.client.get(reverse("admin:article_review_status") + f"?subject_id={self.subj_a2.pk}")
		self.assertTrue(own.context["can_mark"])
		self.assertFalse(other.context["can_mark"])

	def test_staff_on_no_team_cannot_add(self):
		loose = User.objects.create_user(username="ter-loose2", password="pw", is_staff=True)
		OrganizationUser.objects.create(organization=self.org_a, user=loose)
		grant(loose, "add_sources", "view_sources", "add_teamcategory", "view_teamcategory")
		self.client.force_login(loose)
		self.assertEqual(self.client.get(reverse("admin:gregory_sources_add")).status_code, 403)
		self.assertEqual(self.client.get(reverse("admin:gregory_teamcategory_add")).status_code, 403)


class EditorSavesWithInlinesTest(TeamFixture):
	def test_article_with_site_content_and_category_rows(self):
		from django.contrib.sites.models import Site

		from gregory.models import ArticleCategoryAssignment, ArticleSiteContent, OrganizationSite

		site = Site.objects.create(domain="ter2.example.com", name="Ter2")
		OrganizationSite.objects.create(organization=self.org_a, site=site)
		self.by_source.teams.add(self.a1)
		ArticleSiteContent.objects.create(article=self.by_source, site=site, takeaways="Before")
		cat = TeamCategory.objects.create(team=self.a1, category_name="Cat", category_terms=["z"])
		ArticleCategoryAssignment.objects.create(articles=self.by_source, teamcategory=cat)
		url = reverse("admin:gregory_articles_change", args=[self.by_source.pk])
		data = _resubmit_data(self.client.get(url))
		data["subjects"] = [str(self.subj_a1.pk)]
		prefix = [k for k in data if k.endswith("-0-takeaways")][0]
		data[prefix] = "After"
		resp = self.client.post(url, data)
		self.assertEqual(resp.status_code, 302)
		self.assertEqual(ArticleSiteContent.objects.get(article=self.by_source).takeaways, "After")
		self.assertTrue(ArticleCategoryAssignment.objects.filter(articles=self.by_source).exists())

	def test_editor_saves_their_own_trial(self):
		trial = Trials.objects.create(title="Own trial", link="https://example.com/ter-own-trial2")
		# A source puts it in the organisation's view; the tag makes it the team's.
		trial.sources.add(Sources.objects.create(name="Trials A2b", source_for="trials", team=self.a2))
		trial.teams.add(self.a1)
		url = reverse("admin:gregory_trials_change", args=[trial.pk])
		data = _resubmit_data(self.client.get(url))
		data["subjects"] = [str(self.subj_a1.pk)]
		data["teams"] = [str(self.a1.pk)]
		resp = self.client.post(url, data)
		self.assertEqual(resp.status_code, 302)
		self.assertEqual(set(trial.subjects.all()), {self.subj_a1})
