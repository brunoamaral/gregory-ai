"""
The `confirmed` checkbox on an article's trial links (ArticleTrialReferenceInline), the
read-only column on the trial page, and the review list page (ArticleTrialReferenceAdmin)
with its "Confirm selected links" and "Hide selected links" actions.

Run with:
    docker exec gregory python manage.py test gregory.tests.test_admin_trial_link_confirmed
"""

from django.contrib.auth import get_user_model
from django.urls import reverse

from gregory.admin import ArticleTrialReferenceInline, TrialArticleReferenceInline
from gregory.models import ArticleTrialReference
from gregory.tests.test_admin_trial_links import TrialLinkFixture, _formset, _page_data

User = get_user_model()

LIST_URL = "admin:gregory_articletrialreference_changelist"


class ConfirmedInlineTests(TrialLinkFixture):
	def _row_form(self, response, reference):
		forms = _formset(response, ArticleTrialReferenceInline).forms
		return next(f for f in forms if f.instance.pk == reference.pk)

	def _confirm_data(self, response, *references):
		data = _page_data(response)
		prefix = _formset(response, ArticleTrialReferenceInline).prefix
		for i in range(int(data[f"{prefix}-TOTAL_FORMS"][0])):
			if int(data[f"{prefix}-{i}-id"][0]) in {r.pk for r in references}:
				data[f"{prefix}-{i}-confirmed"] = ["on"]
		data["_continue"] = ["Save and continue editing"]
		return data

	def _manual_link(self):
		return ArticleTrialReference.objects.create(
			article=self.article,
			trial=self.other_trial,
			identifier_type="manual",
			identifier_value="X",
			source="manual",
			confirmed=True,
			created_by=self.editor,
		)

	def test_the_box_is_editable_on_a_visible_auto_row_and_disabled_elsewhere(self):
		auto = self._auto_link()
		manual = self._manual_link()
		hidden = ArticleTrialReference.objects.create(
			article=self.article,
			trial=self.foreign_trial,
			identifier_type="nct",
			identifier_value="X",
			suppressed=True,
		)
		response = self._page(self.editor)
		disabled = {
			ref.pk: self._row_form(response, ref).fields["confirmed"].disabled for ref in (auto, manual, hidden)
		}
		self.assertEqual(disabled, {auto.pk: False, manual.pk: True, hidden.pk: True})

	def test_ticking_it_confirms_the_row_and_history_names_the_user(self):
		auto = self._auto_link()
		response = self._page(self.editor)

		post = self.client.post(self.url, self._confirm_data(response, auto))

		self.assertSaved(post)
		auto.refresh_from_db()
		self.assertTrue(auto.confirmed)
		latest = ArticleTrialReference.history.filter(id=auto.id).latest("history_date")
		self.assertEqual((latest.confirmed, latest.editor_user, latest.via), (True, self.editor, "admin"))

	def test_unticking_it_clears_the_flag(self):
		auto = self._auto_link()
		auto.confirmed = True
		auto.save()
		response = self._page(self.editor)
		data = _page_data(response)  # the box is ticked in the page, so it is posted
		prefix = _formset(response, ArticleTrialReferenceInline).prefix
		self.assertIn(f"{prefix}-0-confirmed", data)
		del data[f"{prefix}-0-confirmed"]
		data["_continue"] = ["Save and continue editing"]

		self.assertSaved(self.client.post(self.url, data))

		auto.refresh_from_db()
		self.assertFalse(auto.confirmed)

	def test_a_forged_value_on_a_hidden_row_is_ignored(self):
		hidden = self._auto_link()
		hidden.suppressed = True
		hidden.save()
		response = self._page(self.editor)

		self.assertSaved(self.client.post(self.url, self._confirm_data(response, hidden)))

		hidden.refresh_from_db()
		self.assertFalse(hidden.confirmed)

	def test_a_manual_row_stays_confirmed_whatever_is_posted(self):
		manual = self._manual_link()
		response = self._page(self.editor)
		data = _page_data(response)
		data.pop(f"{_formset(response, ArticleTrialReferenceInline).prefix}-0-confirmed", None)
		data["_continue"] = ["Save and continue editing"]

		self.assertSaved(self.client.post(self.url, data))

		manual.refresh_from_db()
		self.assertTrue(manual.confirmed)

	def test_an_editor_of_another_team_cannot_confirm_from_the_article_page(self):
		auto = self._auto_link()
		editor_page = self._page(self.editor)
		data = self._confirm_data(editor_page, auto)

		self.client.force_login(self.viewer)
		self.assertEqual(self.client.post(self.url, data).status_code, 403)

		auto.refresh_from_db()
		self.assertFalse(auto.confirmed)

	def test_the_box_is_read_only_for_that_other_team(self):
		auto = self._auto_link()
		response = self._page(self.viewer)
		self.assertNotContains(response, "trial_references-0-confirmed")
		self.assertEqual(self._row_form(response, auto).instance.pk, auto.pk)

	def test_the_trial_page_shows_the_column_read_only(self):
		self._auto_link()
		self.assertIn("confirmed", TrialArticleReferenceInline.readonly_fields)
		self.client.force_login(self.superuser)
		response = self.client.get(reverse("admin:gregory_trials_change", args=[self.trial.pk]))
		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, "article_references-0-confirmed")


class ListPageTests(TrialLinkFixture):
	def setUp(self):
		super().setUp()
		self.auto = self._auto_link()
		self.second = ArticleTrialReference.objects.create(
			article=self.article,
			trial=self.other_trial,
			identifier_type="acronym",
			identifier_value="ALPHA",
		)
		self.outsider = User.objects.create_user("tl-outsider", "o@example.com", "x", is_staff=True)
		self.outsider.groups.add(self.editor.groups.first())
		self.org_b.add_user(self.outsider)
		self.team_b.members.add(self.outsider)

	def _list(self, user, query=""):
		self.client.force_login(user)
		response = self.client.get(reverse(LIST_URL) + query)
		self.assertEqual(response.status_code, 200)
		return response

	def _ids(self, response):
		return {row.pk for row in response.context["cl"].result_list}

	def _act(self, user, action, *references):
		self.client.force_login(user)
		return self.client.post(
			reverse(LIST_URL),
			{"action": action, "_selected_action": [str(r.pk) for r in references]},
			follow=True,
		)

	def test_an_editor_sees_the_links_of_their_organisation_only(self):
		self.assertEqual(self._ids(self._list(self.editor)), {self.auto.pk, self.second.pk})
		self.assertEqual(self._ids(self._list(self.outsider)), set())
		self.assertEqual(self._ids(self._list(self.superuser)), {self.auto.pk, self.second.pk})

	def test_filters(self):
		self.auto.confirmed = True
		self.auto.save()
		for query, expected in (
			("?confirmed__exact=0", {self.second.pk}),
			("?confirmed__exact=1", {self.auto.pk}),
			("?identifier_type=acronym", {self.second.pk}),
			("?source__exact=manual", set()),
			("?suppressed__exact=1", set()),
		):
			with self.subTest(query=query):
				self.assertEqual(self._ids(self._list(self.editor, query)), expected)

	def test_there_is_no_add_form(self):
		self.client.force_login(self.superuser)
		self.assertEqual(self.client.get(reverse("admin:gregory_articletrialreference_add")).status_code, 403)

	def test_confirm_selected_links(self):
		response = self._act(self.editor, "confirm_links", self.second)

		self.assertContains(response, "Confirmed 1 link(s).")
		self.second.refresh_from_db()
		self.auto.refresh_from_db()
		self.assertTrue(self.second.confirmed)
		# Pair semantics: the other row of the same pair would follow, a row of another pair does not.
		self.assertFalse(self.auto.confirmed)
		latest = ArticleTrialReference.history.filter(id=self.second.id).latest("history_date")
		self.assertEqual((latest.editor_user, latest.via), (self.editor, "admin"))

	def test_confirming_one_row_confirms_the_whole_pair(self):
		sibling = ArticleTrialReference.objects.create(
			article=self.article, trial=self.trial, identifier_type="eudract", identifier_value="X"
		)
		self._act(self.editor, "confirm_links", self.auto)
		sibling.refresh_from_db()
		self.assertTrue(sibling.confirmed)

	def test_hide_selected_links(self):
		self.auto.confirmed = True
		self.auto.save()

		response = self._act(self.editor, "hide_links", self.auto)

		self.assertContains(response, "Hid 1 link(s).")
		self.auto.refresh_from_db()
		self.assertEqual((self.auto.suppressed, self.auto.confirmed), (True, False))
		self.second.refresh_from_db()
		self.assertFalse(self.second.suppressed)

	def test_hiding_a_manual_link_deletes_it(self):
		manual = ArticleTrialReference.objects.create(
			article=self.article,
			trial=self.foreign_trial,
			identifier_type="manual",
			identifier_value="X",
			source="manual",
			confirmed=True,
		)
		self._act(self.editor, "hide_links", manual)
		self.assertFalse(ArticleTrialReference.objects.filter(pk=manual.pk).exists())

	def test_an_editor_of_another_team_cannot_confirm_or_hide(self):
		for action in ("confirm_links", "hide_links"):
			with self.subTest(action=action):
				response = self._act(self.viewer, action, self.auto, self.second)
				self.assertContains(response, "belong to another team")
				self.auto.refresh_from_db()
				self.second.refresh_from_db()
				self.assertEqual(
					(self.auto.confirmed, self.auto.suppressed, self.second.confirmed, self.second.suppressed),
					(False, False, False, False),
				)

	def test_another_organisation_cannot_act_on_the_rows(self):
		self._act(self.outsider, "confirm_links", self.auto)
		self.auto.refresh_from_db()
		self.assertFalse(self.auto.confirmed)

	def test_only_superusers_delete_from_the_list(self):
		self.client.force_login(self.editor)
		response = self.client.get(reverse(LIST_URL))
		choices = [value for value, _label in response.context["action_form"].fields["action"].choices]
		self.assertEqual(sorted(choices), ["", "confirm_links", "hide_links"])
		self.assertIn("delete_selected", [v for v, _ in self._list(self.superuser).context["action_form"].fields["action"].choices])
