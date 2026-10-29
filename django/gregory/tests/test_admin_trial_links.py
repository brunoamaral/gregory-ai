"""
Tests for linking an article to a clinical trial by hand in the Django admin:
the "Add a trial link" inline (gregory.admin.ArticleTrialLinkAddInline), the delete
boxes on the article's trial list (ArticleTrialReferenceInline), and the trial picker
(TrialAdmin.get_search_results and gregory.autocomplete).

The rules are the editor API's (gregory.services.trial_links): a manual link is deleted
when removed, an auto-detected one is hidden so `detect_trial_references` doesn't recreate
it.

Run with:
    docker exec gregory python manage.py test gregory.tests.test_admin_trial_links
"""

from bs4 import BeautifulSoup
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse
from organizations.models import Organization

from gregory.admin import ArticleTrialLinkAddInline, ArticleTrialReferenceInline
from gregory.models import ArticleTrialReference, Articles, Sources, Subject, Team, Trials
from gregory.roles import sync_editor_group
from gregory.services.trial_links import trial_label

User = get_user_model()

NCT_A = "NCT01234567"
NCT_OTHER = "NCT07654321"


def _page_data(response):
	"""What the browser would post back for the change page in `response`: every named
	control that isn't disabled, a checkbox only when ticked."""
	form = BeautifulSoup(response.content, "html.parser").find("form", id="articles_form")
	data = {}
	for control in form.find_all(["input", "select", "textarea"]):
		name = control.get("name")
		if not name or "__prefix__" in name or control.has_attr("disabled"):
			continue
		if control.name == "select":
			values = [o.get("value", o.text) for o in control.find_all("option", selected=True)]
			if not control.has_attr("multiple") and not values:
				first = control.find("option")
				values = [first.get("value", first.text)] if first else [""]
		elif control.name == "textarea":
			values = [control.text.lstrip("\n")]
		else:
			kind = (control.get("type") or "text").lower()
			if kind in {"submit", "button", "file", "image", "reset"}:
				continue
			if kind in {"checkbox", "radio"} and not control.has_attr("checked"):
				continue
			values = [control.get("value", "")]
		data.setdefault(name, []).extend(values)
	return data


def _formset(response, inline_class):
	return next(
		f for f in response.context["inline_admin_formsets"] if isinstance(f.opts, inline_class)
	).formset


class TrialLinkFixture(TestCase):
	def setUp(self):
		group, _added, _removed = sync_editor_group()

		self.org_a = Organization.objects.create(name="Tl Org A", slug="tl-org-a")
		self.org_b = Organization.objects.create(name="Tl Org B", slug="tl-org-b")
		self.team_a = Team.objects.create(organization=self.org_a, name="Tl A", slug="tl-a")
		self.team_a2 = Team.objects.create(organization=self.org_a, name="Tl A2", slug="tl-a2")
		self.team_b = Team.objects.create(organization=self.org_b, name="Tl B", slug="tl-b")
		self.paper_source = Sources.objects.create(
			name="Tl papers", source_for="science paper", team=self.team_a
		)
		self.trial_source_a = Sources.objects.create(
			name="Tl trials A", source_for="trials", team=self.team_a
		)
		self.trial_source_b = Sources.objects.create(
			name="Tl trials B", source_for="trials", team=self.team_b
		)

		self.article = Articles.objects.create(
			title="Tl article", link="https://tl.test/article", summary=f"Registered as {NCT_A}."
		)
		self.article.sources.add(self.paper_source)
		# The article form requires both.
		self.article.teams.add(self.team_a)
		self.article.subjects.add(
			Subject.objects.create(subject_name="Tl subject", subject_slug="tl-subject", team=self.team_a)
		)
		self.trial = Trials.objects.create(
			title="A trial of something long enough to need a title",
			acronym="ALPHA",
			link="https://tl.test/trial-a",
			identifiers={"nct": NCT_A},
			summary="uniquesummaryword lives only in the summary",
		)
		self.trial.sources.add(self.trial_source_a)
		self.other_trial = Trials.objects.create(
			title="A second trial", link="https://tl.test/trial-2", identifiers={"nct": NCT_OTHER}
		)
		self.other_trial.sources.add(self.trial_source_a)
		self.foreign_trial = Trials.objects.create(
			title="Another organisation's trial",
			link="https://tl.test/trial-b",
			identifiers={"nct": "NCT09999999"},
		)
		self.foreign_trial.sources.add(self.trial_source_b)

		# Edits the article: on the team its source belongs to.
		self.editor = User.objects.create_user("tl-editor", "e@example.com", "x", is_staff=True)
		self.editor.groups.add(group)
		self.org_a.add_user(self.editor)
		self.team_a.members.add(self.editor)
		# Sees the article (same organisation) but is on another team: can't edit it.
		self.viewer = User.objects.create_user("tl-viewer", "v@example.com", "x", is_staff=True)
		self.viewer.groups.add(group)
		self.org_a.add_user(self.viewer)
		self.team_a2.members.add(self.viewer)
		self.superuser = User.objects.create_superuser("tl-root", "r@example.com", "x")

		self.url = reverse("admin:gregory_articles_change", args=[self.article.pk])

	# --- helpers -------------------------------------------------------

	def _page(self, user):
		self.client.force_login(user)
		response = self.client.get(self.url)
		self.assertEqual(response.status_code, 200)
		return response

	def _add_data(self, response, *trials):
		"""The page's own data plus one new row per trial in the add inline."""
		data = _page_data(response)
		prefix = _formset(response, ArticleTrialLinkAddInline).prefix
		data[f"{prefix}-TOTAL_FORMS"] = [str(len(trials))]
		data[f"{prefix}-INITIAL_FORMS"] = ["0"]
		for i, trial in enumerate(trials):
			data[f"{prefix}-{i}-id"] = [""]
			data[f"{prefix}-{i}-article"] = [str(self.article.pk)]
			data[f"{prefix}-{i}-trial"] = [str(trial.pk)]
		data["_continue"] = ["Save and continue editing"]
		return data

	def _delete_data(self, response, *references):
		"""The page's own data with the delete box ticked on each of `references`."""
		data = _page_data(response)
		prefix = _formset(response, ArticleTrialReferenceInline).prefix
		for i in range(int(data[f"{prefix}-TOTAL_FORMS"][0])):
			if int(data[f"{prefix}-{i}-id"][0]) in {r.pk for r in references}:
				data[f"{prefix}-{i}-DELETE"] = ["on"]
		data["_continue"] = ["Save and continue editing"]
		return data

	def assertSaved(self, response):
		"""The admin redirected after a save; if not, say what it complained about."""
		if response.status_code != 302:
			errors = response.context["errors"] if response.context else response.status_code
			self.fail(f"not saved ({response.status_code}): {errors}")

	def _auto_link(self, trial=None):
		return ArticleTrialReference.objects.create(
			article=self.article,
			trial=trial or self.trial,
			identifier_type="nct",
			identifier_value=NCT_A,
		)


class AddTrialLinkTests(TrialLinkFixture):
	def test_adding_creates_a_manual_row_attributed_to_the_user_via_admin(self):
		response = self._page(self.editor)
		post = self.client.post(self.url, self._add_data(response, self.trial))
		self.assertSaved(post)

		link = ArticleTrialReference.objects.get(article=self.article, trial=self.trial)
		self.assertEqual(link.source, ArticleTrialReference.SOURCE_MANUAL)
		self.assertEqual(link.identifier_type, "manual")
		self.assertEqual(link.identifier_value, NCT_A)
		self.assertEqual(link.created_by, self.editor)
		self.assertFalse(link.suppressed)

		row = ArticleTrialReference.history.get(id=link.id)
		self.assertEqual(row.editor_user, self.editor)
		self.assertEqual(row.via, "admin")

	def test_the_new_link_shows_in_the_list_with_a_readable_label(self):
		response = self._page(self.editor)
		self.client.post(self.url, self._add_data(response, self.trial))
		page = self.client.get(self.url)
		self.assertContains(page, f"{NCT_A} · ALPHA · A trial of something")

	def test_adding_a_pair_that_is_already_linked_creates_nothing(self):
		auto = self._auto_link()
		response = self._page(self.editor)
		post = self.client.post(self.url, self._add_data(response, self.trial))
		self.assertEqual(post.status_code, 200)
		self.assertContains(post, "This trial is already linked to the article.")
		self.assertEqual(list(ArticleTrialReference.objects.all()), [auto])

	def test_the_same_trial_twice_in_one_save_is_an_error(self):
		response = self._page(self.editor)
		post = self.client.post(self.url, self._add_data(response, self.trial, self.trial))
		self.assertEqual(post.status_code, 200)
		self.assertContains(post, "This trial is listed twice.")
		self.assertFalse(ArticleTrialReference.objects.exists())

	def test_adding_a_pair_whose_auto_link_is_hidden_creates_a_manual_row_next_to_it(self):
		hidden = self._auto_link()
		hidden.suppressed = True
		hidden.save()
		response = self._page(self.editor)
		self.assertSaved(self.client.post(self.url, self._add_data(response, self.trial)))
		self.assertEqual(
			set(ArticleTrialReference.objects.values_list("source", "suppressed")),
			{("auto", True), ("manual", False)},
		)

	def test_a_superuser_can_add_a_link_too(self):
		response = self._page(self.superuser)
		self.assertSaved(self.client.post(self.url, self._add_data(response, self.trial)))
		self.assertEqual(ArticleTrialReference.objects.get().created_by, self.superuser)

	def test_the_add_page_of_a_new_article_still_renders(self):
		self.client.force_login(self.superuser)
		self.assertEqual(self.client.get(reverse("admin:gregory_articles_add")).status_code, 200)


class RemoveTrialLinkTests(TrialLinkFixture):
	def test_removing_a_manual_link_deletes_it(self):
		manual = ArticleTrialReference.objects.create(
			article=self.article,
			trial=self.trial,
			identifier_type="manual",
			identifier_value=NCT_A,
			source=ArticleTrialReference.SOURCE_MANUAL,
			created_by=self.editor,
		)
		response = self._page(self.editor)
		post = self.client.post(self.url, self._delete_data(response, manual))
		self.assertSaved(post)
		self.assertFalse(ArticleTrialReference.objects.exists())

	def test_removing_an_auto_link_hides_it_and_detection_does_not_bring_it_back(self):
		auto = self._auto_link()
		response = self._page(self.editor)
		post = self.client.post(self.url, self._delete_data(response, auto))
		self.assertSaved(post)

		auto.refresh_from_db()  # still there
		self.assertTrue(auto.suppressed)
		self.assertEqual(auto.source, ArticleTrialReference.SOURCE_AUTO)
		row = ArticleTrialReference.history.filter(id=auto.id).first()
		self.assertEqual((row.via, row.editor_user), ("admin", self.editor))

		call_command("detect_trial_references")
		self.assertEqual(list(ArticleTrialReference.objects.all()), [auto])
		auto.refresh_from_db()
		self.assertTrue(auto.suppressed)

	def test_a_hidden_row_stays_listed_without_a_delete_box(self):
		hidden = self._auto_link()
		hidden.suppressed = True
		hidden.save()
		visible = self._auto_link(self.other_trial)
		response = self._page(self.editor)

		forms = _formset(response, ArticleTrialReferenceInline).forms
		boxes = {f.instance.pk: f["DELETE"].field.widget.is_hidden for f in forms}
		self.assertEqual(boxes, {hidden.pk: True, visible.pk: False})
		# And a forged delete of it changes nothing.
		data = self._delete_data(response, hidden)
		self.assertSaved(self.client.post(self.url, data))
		hidden.refresh_from_db()
		self.assertTrue(hidden.suppressed)


class PermissionTests(TrialLinkFixture):
	def test_an_editor_of_the_article_sees_the_add_row_and_delete_boxes(self):
		self._auto_link()
		response = self._page(self.editor)
		self.assertContains(response, "Add another Trial link")
		self.assertContains(response, "trial_references-0-DELETE")

	def test_a_viewer_sees_the_links_but_no_add_row_or_delete_boxes(self):
		self._auto_link()
		response = self._page(self.viewer)
		self.assertContains(response, NCT_A)  # the list is there
		self.assertNotContains(response, "Add another Trial link")
		self.assertNotContains(response, "Add a trial link")
		self.assertNotContains(response, "-DELETE")
		self.assertEqual(
			[type(f.opts) for f in response.context["inline_admin_formsets"] if f.opts.model is ArticleTrialReference],
			[ArticleTrialReferenceInline],
		)

	def test_a_crafted_post_by_a_viewer_is_refused_and_changes_nothing(self):
		auto = self._auto_link()
		editor_page = self._page(self.editor)
		add = self._add_data(editor_page, self.other_trial)
		delete = self._delete_data(editor_page, auto)

		self.client.force_login(self.viewer)
		for data in (add, delete):
			self.assertEqual(self.client.post(self.url, data).status_code, 403)
		auto.refresh_from_db()
		self.assertFalse(auto.suppressed)
		self.assertEqual(ArticleTrialReference.objects.count(), 1)

	def test_the_formsets_refuse_a_viewer_even_past_the_page_check(self):
		"""The page-level refusal above is Django's. The inlines carry their own, so a
		viewer's data is dropped even if something let the request through."""
		auto = self._auto_link()
		request = RequestFactory().get("/")
		request.user = self.viewer
		for inline_class, extra in (
			(ArticleTrialLinkAddInline, {"0-article": self.article.pk, "0-trial": self.other_trial.pk}),
			(ArticleTrialReferenceInline, {"0-id": auto.pk, "0-article": self.article.pk, "0-DELETE": "on"}),
		):
			inline = inline_class(Articles, admin.site)
			formset_class = inline.get_formset(request, self.article)
			prefix = formset_class.get_default_prefix()
			initial = 1 if inline_class is ArticleTrialReferenceInline else 0
			data = {
				f"{prefix}-TOTAL_FORMS": "1",
				f"{prefix}-INITIAL_FORMS": str(initial),
				f"{prefix}-MIN_NUM_FORMS": "0",
				f"{prefix}-MAX_NUM_FORMS": "1000",
				**{f"{prefix}-{k}": str(v) for k, v in extra.items()},
			}
			formset = formset_class(
				data, instance=self.article, prefix=prefix, queryset=inline.get_queryset(request)
			)
			if formset.is_valid():
				formset.save()
		auto.refresh_from_db()
		self.assertFalse(auto.suppressed)
		self.assertEqual(ArticleTrialReference.objects.count(), 1)

	def test_a_trial_outside_the_organisation_is_refused_by_the_form_field(self):
		response = self._page(self.editor)
		post = self.client.post(self.url, self._add_data(response, self.foreign_trial))
		self.assertEqual(post.status_code, 200)
		self.assertContains(post, "Select a valid choice")
		self.assertFalse(ArticleTrialReference.objects.exists())

	def test_a_superuser_can_pick_any_trial(self):
		response = self._page(self.superuser)
		self.assertSaved(self.client.post(self.url, self._add_data(response, self.foreign_trial)))
		self.assertTrue(ArticleTrialReference.objects.filter(trial=self.foreign_trial).exists())


class TrialPickerTests(TrialLinkFixture):
	def _search(self, user, term):
		self.client.force_login(user)
		response = self.client.get(
			reverse("admin:autocomplete"),
			{
				"term": term,
				"app_label": "gregory",
				"model_name": "articletrialreference",
				"field_name": "trial",
			},
		)
		self.assertEqual(response.status_code, 200)
		return response.json()["results"]

	def test_finds_a_trial_by_a_pasted_registry_id(self):
		results = self._search(self.editor, NCT_A)
		self.assertEqual([r["id"] for r in results], [str(self.trial.pk)])

	def test_finds_a_trial_by_acronym_title_and_database_id(self):
		for term in ("alpha", "need a title", str(self.trial.pk)):
			with self.subTest(term=term):
				self.assertIn(str(self.trial.pk), [r["id"] for r in self._search(self.editor, term)])

	def test_every_word_must_match(self):
		self.assertEqual(self._search(self.editor, "alpha nomatchword"), [])

	def test_does_not_search_the_summary(self):
		self.assertEqual(self._search(self.editor, "uniquesummaryword"), [])

	def test_the_changelist_search_still_searches_the_summary(self):
		self.client.force_login(self.superuser)
		response = self.client.get(reverse("admin:gregory_trials_changelist"), {"q": "uniquesummaryword"})
		self.assertContains(response, "A trial of something")

	def test_the_label_reads_registry_id_acronym_title(self):
		(result,) = self._search(self.editor, NCT_A)
		self.assertEqual(result["text"], trial_label(self.trial))
		self.assertTrue(result["text"].startswith(f"{NCT_A} · ALPHA · A trial of"))

	def test_other_models_keep_their_own_label(self):
		self.client.force_login(self.superuser)
		response = self.client.get(
			reverse("admin:autocomplete"),
			{"term": "", "app_label": "gregory", "model_name": "articlecategoryassignment", "field_name": "teamcategory"},
		)
		self.assertEqual(response.status_code, 200)

	def test_does_not_offer_another_organisations_trial(self):
		self.assertEqual(self._search(self.editor, "NCT09999999"), [])
		self.assertEqual(len(self._search(self.superuser, "NCT09999999")), 1)


class TrialLabelTests(TestCase):
	def test_leaves_out_what_is_missing_and_truncates_the_title(self):
		long_title = "x" * 200
		trial = Trials(title=long_title, acronym=None, identifiers={"isrctn": "ISRCTN123"})
		label = trial_label(trial)
		self.assertEqual(label, "ISRCTN123 · " + "x" * 89 + "…")
		self.assertEqual(trial_label(Trials(title="Plain", identifiers=None, trial_id=7)), "trial:7 · Plain")
