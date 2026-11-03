"""
Tests for linking a patent to a clinical trial by hand in the Django admin: the "Add a
patent link" inline on the trial page and the "Add a trial link" inline on the patent page
(gregory.admin.TrialPatentLinkAddInline, PatentTrialLinkAddInline), the delete boxes on
each page's link list (TrialPatentLinkInline, PatentTrialLinkInline), the pickers, the
`team_editable` rule for a link and the standalone list's default filter.

The rules are the editor API's (gregory.services.patent_trial_links): a manual link is
deleted when removed, a detected one is hidden so `detect_patent_trial_links` doesn't
recreate it.

Run with:
    docker exec gregory python manage.py test gregory.tests.test_admin_patent_trial_links
"""

import datetime

from bs4 import BeautifulSoup
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse
from organizations.models import Organization

from gregory.admin import (
	PatentTrialLinkAddInline,
	PatentTrialLinkInline,
	TrialPatentLinkAddInline,
	TrialPatentLinkInline,
)
from gregory.models import (
	PatentTrialLink,
	Patents,
	Sources,
	Subject,
	Team,
	Trials,
)
from gregory.roles import sync_editor_group
from gregory.services.patent_trial_links import patent_label
from gregory.services.trial_links import trial_label
from gregory.team_scope import team_editable

User = get_user_model()
D = datetime.date

NCT_A = "NCT01234567"


def _page_data(response, form_id):
	"""What the browser would post back for the change page in `response`: every named
	control that isn't disabled, a checkbox only when ticked."""
	form = BeautifulSoup(response.content, "html.parser").find("form", id=form_id)
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


class PatentTrialLinkFixture(TestCase):
	def setUp(self):
		group, _added, _removed = sync_editor_group()

		self.org_a = Organization.objects.create(name="Ptl Org A", slug="ptl-org-a")
		self.org_b = Organization.objects.create(name="Ptl Org B", slug="ptl-org-b")
		self.team_a = Team.objects.create(organization=self.org_a, name="Ptl A", slug="ptl-a")
		self.team_a2 = Team.objects.create(organization=self.org_a, name="Ptl A2", slug="ptl-a2")
		self.team_b = Team.objects.create(organization=self.org_b, name="Ptl B", slug="ptl-b")
		self.subject = Subject.objects.create(
			subject_name="Ptl subject", subject_slug="ptl-subject", team=self.team_a
		)
		self.trial_source_a = Sources.objects.create(name="Ptl trials A", source_for="trials", team=self.team_a)
		self.trial_source_a2 = Sources.objects.create(name="Ptl trials A2", source_for="trials", team=self.team_a2)
		self.trial_source_b = Sources.objects.create(name="Ptl trials B", source_for="trials", team=self.team_b)
		self.patent_source_a = Sources.objects.create(name="Ptl patents A", source_for="patents", team=self.team_a)
		self.patent_source_a2 = Sources.objects.create(name="Ptl patents A2", source_for="patents", team=self.team_a2)
		self.patent_source_b = Sources.objects.create(name="Ptl patents B", source_for="patents", team=self.team_b)

		# Trial 31834 and patent 2335 of the spec: filed 1,241 days before the trial.
		self.trial = Trials.objects.create(
			title="A trial of something long enough to need a title",
			acronym="ALPHA",
			link="https://ptl.test/trial-a",
			identifiers={"nct": NCT_A},
			recruitment_status="Recruiting",
			date_enrollement=D(2026, 10, 1),
		)
		self.trial.sources.add(self.trial_source_a)
		self.trial.teams.add(self.team_a)
		self.trial.subjects.add(self.subject)
		self.other_trial = Trials.objects.create(
			title="A second trial", link="https://ptl.test/trial-2", identifiers={"nct": "NCT07654321"}
		)
		self.other_trial.sources.add(self.trial_source_a)
		self.foreign_trial = Trials.objects.create(
			title="Another organisation's trial", link="https://ptl.test/trial-b", identifiers={"nct": "NCT09999999"}
		)
		self.foreign_trial.sources.add(self.trial_source_b)

		self.patent = Patents.objects.create(
			family_id="ptl-1",
			title="A patent on a remyelination drug",
			representative_publication="WO2023123456",
			earliest_priority_date=D(2023, 5, 9),
		)
		self.patent.sources.add(self.patent_source_a)
		self.patent.teams.add(self.team_a)
		self.patent.subjects.add(self.subject)
		self.other_patent = Patents.objects.create(
			family_id="ptl-2", title="A second patent", earliest_priority_date=D(2022, 10, 5)
		)
		self.other_patent.sources.add(self.patent_source_a)
		self.foreign_patent = Patents.objects.create(family_id="ptl-3", title="Another organisation's patent")
		self.foreign_patent.sources.add(self.patent_source_b)

		# Edits both pages: on team A, which owns the trial's and the patent's sources.
		self.editor = User.objects.create_user("ptl-editor", "e@example.com", "x", is_staff=True)
		self.editor.groups.add(group)
		self.org_a.add_user(self.editor)
		self.team_a.members.add(self.editor)
		# Sees both (same organisation) but is on another team: can't edit either.
		self.viewer = User.objects.create_user("ptl-viewer", "v@example.com", "x", is_staff=True)
		self.viewer.groups.add(group)
		self.org_a.add_user(self.viewer)
		self.team_a2.members.add(self.viewer)
		self.superuser = User.objects.create_superuser("ptl-root", "r@example.com", "x")

		self.trial_url = reverse("admin:gregory_trials_change", args=[self.trial.pk])
		self.patent_url = reverse("admin:gregory_patents_change", args=[self.patent.pk])

	# --- helpers -------------------------------------------------------

	def _page(self, user, url):
		self.client.force_login(user)
		response = self.client.get(url)
		self.assertEqual(response.status_code, 200)
		return response

	def _trial_add_data(self, response, *patents):
		"""The trial page's own data plus one new row per patent in its add inline."""
		data = _page_data(response, "trials_form")
		prefix = _formset(response, TrialPatentLinkAddInline).prefix
		data[f"{prefix}-TOTAL_FORMS"] = [str(len(patents))]
		data[f"{prefix}-INITIAL_FORMS"] = ["0"]
		for i, patent in enumerate(patents):
			data[f"{prefix}-{i}-id"] = [""]
			data[f"{prefix}-{i}-trial"] = [str(self.trial.pk)]
			data[f"{prefix}-{i}-patent"] = [str(patent.pk)]
		data["_continue"] = ["Save and continue editing"]
		return data

	def _patent_add_data(self, response, *trials):
		"""The patent page's own data plus one new row per trial in its add inline."""
		data = _page_data(response, "patents_form")
		prefix = _formset(response, PatentTrialLinkAddInline).prefix
		data[f"{prefix}-TOTAL_FORMS"] = [str(len(trials))]
		data[f"{prefix}-INITIAL_FORMS"] = ["0"]
		for i, trial in enumerate(trials):
			data[f"{prefix}-{i}-id"] = [""]
			data[f"{prefix}-{i}-patent"] = [str(self.patent.pk)]
			data[f"{prefix}-{i}-trial"] = [str(trial.pk)]
		data["_continue"] = ["Save and continue editing"]
		return data

	def _delete_data(self, response, form_id, inline_class, *links):
		"""The page's own data with the delete box ticked on each of `links`."""
		data = _page_data(response, form_id)
		prefix = _formset(response, inline_class).prefix
		for i in range(int(data[f"{prefix}-TOTAL_FORMS"][0])):
			if int(data[f"{prefix}-{i}-id"][0]) in {link.pk for link in links}:
				data[f"{prefix}-{i}-DELETE"] = ["on"]
		data["_continue"] = ["Save and continue editing"]
		return data

	def assertSaved(self, response):
		"""The admin redirected after a save; if not, say what it complained about."""
		if response.status_code != 302:
			errors = response.context["errors"] if response.context else response.status_code
			self.fail(f"not saved ({response.status_code}): {errors}")

	def _link(self, patent=None, trial=None, **kwargs):
		return PatentTrialLink.objects.create(
			patent=patent or self.patent,
			trial=trial or self.trial,
			**{
				"sponsor": None,
				"timing": PatentTrialLink.TIMING_DURING,
				"basis": PatentTrialLink.BASIS_SUBJECT,
				**kwargs,
			},
		)

	def _manual_link(self, patent=None, trial=None):
		return self._link(
			patent, trial, source=PatentTrialLink.SOURCE_MANUAL, created_by=self.editor
		)


class AddFromTheTrialPageTests(PatentTrialLinkFixture):
	def test_adding_creates_a_manual_row_attributed_to_the_user_via_admin(self):
		response = self._page(self.editor, self.trial_url)
		post = self.client.post(self.trial_url, self._trial_add_data(response, self.patent))
		self.assertSaved(post)

		link = PatentTrialLink.objects.get(patent=self.patent, trial=self.trial)
		self.assertEqual(link.source, PatentTrialLink.SOURCE_MANUAL)
		self.assertEqual(link.created_by, self.editor)
		self.assertFalse(link.suppressed)
		# Worked out from the data when the link is made: filed 1,241 days before.
		self.assertEqual(link.timing, PatentTrialLink.TIMING_BEFORE)
		self.assertEqual(link.days_before_start, 1241)
		self.assertEqual(link.basis, PatentTrialLink.BASIS_SUBJECT)
		self.assertIsNone(link.sponsor)

		row = PatentTrialLink.history.get(id=link.id)
		self.assertEqual(row.editor_user, self.editor)
		self.assertEqual(row.via, "admin")

	def test_the_new_link_shows_in_the_list_with_a_readable_label(self):
		response = self._page(self.editor, self.trial_url)
		self.client.post(self.trial_url, self._trial_add_data(response, self.patent))
		page = self.client.get(self.trial_url)
		self.assertContains(page, "WO2023123456 · A patent on a remyelination drug")

	def test_a_pair_that_is_already_linked_creates_nothing(self):
		auto = self._link()
		response = self._page(self.editor, self.trial_url)
		post = self.client.post(self.trial_url, self._trial_add_data(response, self.patent))
		self.assertEqual(post.status_code, 200)
		self.assertContains(post, "This patent is already linked to the trial.")
		self.assertEqual(list(PatentTrialLink.objects.all()), [auto])

	def test_the_same_patent_twice_in_one_save_is_an_error(self):
		response = self._page(self.editor, self.trial_url)
		post = self.client.post(self.trial_url, self._trial_add_data(response, self.patent, self.patent))
		self.assertEqual(post.status_code, 200)
		self.assertContains(post, "This patent is listed twice.")
		self.assertFalse(PatentTrialLink.objects.exists())

	def test_a_pair_whose_detected_link_is_hidden_is_brought_back_as_manual(self):
		hidden = self._link(suppressed=True, basis=PatentTrialLink.BASIS_CATEGORY)
		response = self._page(self.editor, self.trial_url)
		self.assertSaved(self.client.post(self.trial_url, self._trial_add_data(response, self.patent)))
		hidden.refresh_from_db()
		self.assertEqual(
			(hidden.suppressed, hidden.source, hidden.created_by),
			(False, PatentTrialLink.SOURCE_MANUAL, self.editor),
		)
		self.assertEqual(PatentTrialLink.objects.count(), 1)

	def test_a_superuser_can_add_a_link_too(self):
		response = self._page(self.superuser, self.trial_url)
		self.assertSaved(self.client.post(self.trial_url, self._trial_add_data(response, self.patent)))
		self.assertEqual(PatentTrialLink.objects.get().created_by, self.superuser)

	def test_the_add_page_of_a_new_trial_still_renders(self):
		self.client.force_login(self.superuser)
		self.assertEqual(self.client.get(reverse("admin:gregory_trials_add")).status_code, 200)


class AddFromThePatentPageTests(PatentTrialLinkFixture):
	def test_adding_creates_a_manual_row_attributed_to_the_user_via_admin(self):
		response = self._page(self.editor, self.patent_url)
		post = self.client.post(self.patent_url, self._patent_add_data(response, self.trial))
		self.assertSaved(post)

		link = PatentTrialLink.objects.get(patent=self.patent, trial=self.trial)
		self.assertEqual(link.source, PatentTrialLink.SOURCE_MANUAL)
		self.assertEqual(link.created_by, self.editor)
		self.assertEqual((link.timing, link.days_before_start), ("before", 1241))
		row = PatentTrialLink.history.get(id=link.id)
		self.assertEqual((row.editor_user, row.via), (self.editor, "admin"))

	def test_the_new_link_shows_in_the_list_with_a_readable_label(self):
		response = self._page(self.editor, self.patent_url)
		self.client.post(self.patent_url, self._patent_add_data(response, self.trial))
		self.assertContains(self.client.get(self.patent_url), f"{NCT_A} · ALPHA · A trial of")

	def test_a_pair_that_is_already_linked_creates_nothing(self):
		auto = self._link()
		response = self._page(self.editor, self.patent_url)
		post = self.client.post(self.patent_url, self._patent_add_data(response, self.trial))
		self.assertEqual(post.status_code, 200)
		self.assertContains(post, "This trial is already linked to the patent.")
		self.assertEqual(list(PatentTrialLink.objects.all()), [auto])

	def test_the_same_trial_twice_in_one_save_is_an_error(self):
		response = self._page(self.editor, self.patent_url)
		post = self.client.post(self.patent_url, self._patent_add_data(response, self.trial, self.trial))
		self.assertEqual(post.status_code, 200)
		self.assertContains(post, "This trial is listed twice.")
		self.assertFalse(PatentTrialLink.objects.exists())

	def test_a_trial_outside_the_organisation_is_refused_by_the_form_field(self):
		response = self._page(self.editor, self.patent_url)
		post = self.client.post(self.patent_url, self._patent_add_data(response, self.foreign_trial))
		self.assertEqual(post.status_code, 200)
		self.assertContains(post, "Select a valid choice")
		self.assertFalse(PatentTrialLink.objects.exists())

	def test_a_superuser_can_pick_any_trial(self):
		response = self._page(self.superuser, self.patent_url)
		self.assertSaved(self.client.post(self.patent_url, self._patent_add_data(response, self.foreign_trial)))
		self.assertTrue(PatentTrialLink.objects.filter(trial=self.foreign_trial).exists())


class RemoveLinkTests(PatentTrialLinkFixture):
	def test_removing_a_manual_link_from_the_trial_page_deletes_it(self):
		manual = self._manual_link()
		response = self._page(self.editor, self.trial_url)
		data = self._delete_data(response, "trials_form", TrialPatentLinkInline, manual)
		self.assertSaved(self.client.post(self.trial_url, data))
		self.assertFalse(PatentTrialLink.objects.exists())
		self.assertEqual(
			PatentTrialLink.history.filter(id=manual.id).order_by("-history_id").first().history_type, "-"
		)

	def test_removing_a_manual_link_from_the_patent_page_deletes_it(self):
		manual = self._manual_link()
		response = self._page(self.editor, self.patent_url)
		data = self._delete_data(response, "patents_form", PatentTrialLinkInline, manual)
		self.assertSaved(self.client.post(self.patent_url, data))
		self.assertFalse(PatentTrialLink.objects.exists())

	def test_removing_a_detected_link_hides_it_and_detection_does_not_bring_it_back(self):
		auto = self._link()
		response = self._page(self.editor, self.trial_url)
		data = self._delete_data(response, "trials_form", TrialPatentLinkInline, auto)
		self.assertSaved(self.client.post(self.trial_url, data))

		auto.refresh_from_db()  # still there
		self.assertTrue(auto.suppressed)
		self.assertEqual(auto.source, PatentTrialLink.SOURCE_AUTO)
		row = PatentTrialLink.history.filter(id=auto.id).first()
		self.assertEqual((row.via, row.editor_user), ("admin", self.editor))

		call_command("detect_patent_trial_links")
		self.assertEqual(list(PatentTrialLink.objects.all()), [auto])
		auto.refresh_from_db()
		self.assertTrue(auto.suppressed)

	def test_a_hidden_row_stays_listed_without_a_delete_box(self):
		hidden = self._link(suppressed=True)
		visible = self._link(self.other_patent)
		response = self._page(self.editor, self.trial_url)

		forms = _formset(response, TrialPatentLinkInline).forms
		boxes = {f.instance.pk: f["DELETE"].field.widget.is_hidden for f in forms}
		self.assertEqual(boxes, {hidden.pk: True, visible.pk: False})
		# And a forged delete of it changes nothing.
		data = self._delete_data(response, "trials_form", TrialPatentLinkInline, hidden)
		self.assertSaved(self.client.post(self.trial_url, data))
		hidden.refresh_from_db()
		self.assertTrue(hidden.suppressed)


class PermissionTests(PatentTrialLinkFixture):
	def test_an_editor_of_the_trial_sees_the_add_row_and_delete_boxes(self):
		self._link()
		response = self._page(self.editor, self.trial_url)
		self.assertContains(response, "Add another Patent link")
		self.assertContains(response, "patent_links-0-DELETE")
		self.assertContains(response, "WO2023123456")

	def test_an_editor_of_the_patent_sees_the_add_row_and_delete_boxes(self):
		self._link()
		response = self._page(self.editor, self.patent_url)
		self.assertContains(response, "Add another Trial link")
		self.assertContains(response, "trial_links-0-DELETE")

	def test_a_viewer_sees_the_links_but_no_add_row_or_delete_boxes(self):
		self._link()
		for url, add_text in ((self.trial_url, "patent link"), (self.patent_url, "trial link")):
			with self.subTest(url=url):
				response = self._page(self.viewer, url)
				self.assertNotContains(response, f"Add another {add_text.title()}")
				self.assertNotContains(response, f"Add a {add_text}")
				self.assertNotContains(response, "-DELETE")
		trial_page = self._page(self.viewer, self.trial_url)
		self.assertContains(trial_page, "WO2023123456")  # the list is there
		self.assertEqual(
			[type(f.opts) for f in trial_page.context["inline_admin_formsets"] if f.opts.model is PatentTrialLink],
			[TrialPatentLinkInline],
		)

	def test_a_crafted_post_by_a_viewer_is_refused_and_changes_nothing(self):
		auto = self._link()
		trial_page = self._page(self.editor, self.trial_url)
		patent_page = self._page(self.editor, self.patent_url)
		attempts = (
			(self.trial_url, self._trial_add_data(trial_page, self.other_patent)),
			(self.trial_url, self._delete_data(trial_page, "trials_form", TrialPatentLinkInline, auto)),
			(self.patent_url, self._patent_add_data(patent_page, self.other_trial)),
			(self.patent_url, self._delete_data(patent_page, "patents_form", PatentTrialLinkInline, auto)),
		)
		self.client.force_login(self.viewer)
		for url, data in attempts:
			self.assertEqual(self.client.post(url, data).status_code, 403)
		auto.refresh_from_db()
		self.assertFalse(auto.suppressed)
		self.assertEqual(PatentTrialLink.objects.count(), 1)

	def test_the_formsets_refuse_a_viewer_even_past_the_page_check(self):
		"""The page-level refusal above is Django's. The inlines carry their own, so a
		viewer's data is dropped even if something let the request through."""
		auto = self._link()
		request = RequestFactory().get("/")
		request.user = self.viewer
		cases = (
			(Trials, self.trial, TrialPatentLinkAddInline, {"0-trial": self.trial.pk, "0-patent": self.other_patent.pk}),
			(Trials, self.trial, TrialPatentLinkInline, {"0-id": auto.pk, "0-trial": self.trial.pk, "0-DELETE": "on"}),
			(Patents, self.patent, PatentTrialLinkAddInline, {"0-patent": self.patent.pk, "0-trial": self.other_trial.pk}),
			(Patents, self.patent, PatentTrialLinkInline, {"0-id": auto.pk, "0-patent": self.patent.pk, "0-DELETE": "on"}),
		)
		for parent_model, parent, inline_class, extra in cases:
			inline = inline_class(parent_model, admin.site)
			formset_class = inline.get_formset(request, parent)
			prefix = formset_class.get_default_prefix()
			initial = 1 if "0-id" in extra else 0
			data = {
				f"{prefix}-TOTAL_FORMS": "1",
				f"{prefix}-INITIAL_FORMS": str(initial),
				f"{prefix}-MIN_NUM_FORMS": "0",
				f"{prefix}-MAX_NUM_FORMS": "1000",
				**{f"{prefix}-{k}": str(v) for k, v in extra.items()},
			}
			formset = formset_class(
				data, instance=parent, prefix=prefix, queryset=inline.get_queryset(request)
			)
			with self.subTest(inline=inline_class.__name__):
				if formset.is_valid():
					formset.save()
		auto.refresh_from_db()
		self.assertFalse(auto.suppressed)
		self.assertEqual(PatentTrialLink.objects.count(), 1)

	def test_a_patent_outside_the_organisation_is_refused_by_the_form_field(self):
		response = self._page(self.editor, self.trial_url)
		post = self.client.post(self.trial_url, self._trial_add_data(response, self.foreign_patent))
		self.assertEqual(post.status_code, 200)
		self.assertContains(post, "Select a valid choice")
		self.assertFalse(PatentTrialLink.objects.exists())

	def test_a_superuser_can_pick_any_patent(self):
		response = self._page(self.superuser, self.trial_url)
		self.assertSaved(self.client.post(self.trial_url, self._trial_add_data(response, self.foreign_patent)))
		self.assertTrue(PatentTrialLink.objects.filter(patent=self.foreign_patent).exists())

	def test_the_editor_role_may_add_and_delete_links(self):
		perms = set(self.editor.get_all_permissions())
		self.assertLessEqual(
			{"gregory.add_patenttriallink", "gregory.delete_patenttriallink"}, perms
		)


class TeamEditableTests(PatentTrialLinkFixture):
	"""A link is editable when the user may edit its patent OR its trial."""

	def setUp(self):
		super().setUp()
		# A patent and a trial that belong to team A2 only.
		self.a2_patent = Patents.objects.create(family_id="ptl-a2", title="A2 patent")
		self.a2_patent.sources.add(self.patent_source_a2)
		self.a2_trial = Trials.objects.create(title="A2 trial", link="https://ptl.test/a2")
		self.a2_trial.sources.add(self.trial_source_a2)

	def _editable(self, user):
		return set(team_editable(user, PatentTrialLink.objects.all()))

	def test_editable_through_the_trial_only(self):
		link = self._link(patent=self.a2_patent, trial=self.trial)
		# Team A owns the trial but not the patent.
		self.assertFalse(team_editable(self.editor, Patents.objects.filter(pk=self.a2_patent.pk)).exists())
		self.assertEqual(self._editable(self.editor), {link})

	def test_editable_through_the_patent_only(self):
		link = self._link(patent=self.patent, trial=self.a2_trial)
		self.assertFalse(team_editable(self.editor, Trials.objects.filter(pk=self.a2_trial.pk)).exists())
		self.assertEqual(self._editable(self.editor), {link})

	def test_editable_through_neither(self):
		link = self._link(patent=self.a2_patent, trial=self.a2_trial)
		self.assertEqual(self._editable(self.editor), set())
		self.assertEqual(self._editable(self.viewer), {link})  # team A2 owns both
		self.assertEqual(self._editable(self.superuser), {link})

	def test_a_user_on_no_team_edits_nothing(self):
		self._link()
		loose = User.objects.create_user("ptl-loose", "l@example.com", "x", is_staff=True)
		self.assertEqual(self._editable(loose), set())

	def test_an_editor_who_owns_only_the_trial_can_edit_the_link_from_its_trial_page(self):
		"""Team A owns the trial but not the patent: the link is theirs from the trial page."""
		link = self._link(patent=self.a2_patent, trial=self.trial)
		response = self._page(self.editor, self.trial_url)
		self.assertContains(response, "patent_links-0-DELETE")
		data = self._delete_data(response, "trials_form", TrialPatentLinkInline, link)
		self.assertSaved(self.client.post(self.trial_url, data))
		link.refresh_from_db()
		self.assertTrue(link.suppressed)


class LinkListTests(PatentTrialLinkFixture):
	def setUp(self):
		super().setUp()
		self.url = reverse("admin:gregory_patenttriallink_changelist")

	def _rows(self, query=""):
		self.client.force_login(self.superuser)
		response = self.client.get(self.url + query)
		self.assertEqual(response.status_code, 200)
		return set(response.context["cl"].queryset)

	def test_the_default_filter_shows_category_links_and_manual_links_of_any_basis(self):
		category = self._link(self.patent, self.trial, basis=PatentTrialLink.BASIS_CATEGORY)
		subject = self._link(self.other_patent, self.trial, basis=PatentTrialLink.BASIS_SUBJECT)
		manual_subject = self._link(
			self.patent, self.other_trial, basis=PatentTrialLink.BASIS_SUBJECT,
			source=PatentTrialLink.SOURCE_MANUAL,
		)
		manual_none = self._link(
			self.other_patent, self.other_trial, basis=None, timing=None,
			source=PatentTrialLink.SOURCE_MANUAL,
		)
		self.assertEqual(self._rows(), {category, manual_subject, manual_none})
		self.assertEqual(self._rows("?basis=all"), {category, subject, manual_subject, manual_none})
		self.assertEqual(self._rows("?basis=subject"), {subject, manual_subject})

	def test_the_list_filters_by_source_and_shows_who_added_a_link(self):
		auto = self._link(basis=PatentTrialLink.BASIS_CATEGORY)
		manual = self._manual_link(self.other_patent, self.trial)
		self.assertEqual(self._rows("?source=manual"), {manual})
		self.assertEqual(self._rows("?source=auto"), {auto})
		self.client.force_login(self.superuser)
		self.assertContains(self.client.get(self.url), "ptl-editor")

	def test_a_manual_link_with_no_sponsor_or_timing_opens_in_the_admin(self):
		manual = self._link(sponsor=None, timing=None, basis=None, source=PatentTrialLink.SOURCE_MANUAL)
		self.client.force_login(self.superuser)
		response = self.client.get(reverse("admin:gregory_patenttriallink_change", args=[manual.pk]))
		self.assertEqual(response.status_code, 200)

	def test_suppress_and_restore_work_on_a_manual_row(self):
		manual = self._manual_link()
		self.client.force_login(self.superuser)
		self.client.post(self.url, {"action": "suppress_links", "_selected_action": [manual.pk]})
		manual.refresh_from_db()
		self.assertTrue(manual.suppressed)
		self.client.post(self.url + "?basis=all", {"action": "restore_links", "_selected_action": [manual.pk]})
		manual.refresh_from_db()
		self.assertFalse(manual.suppressed)


class PickerTests(PatentTrialLinkFixture):
	def _search(self, user, term, model_name, field_name):
		self.client.force_login(user)
		response = self.client.get(
			reverse("admin:autocomplete"),
			{"term": term, "app_label": "gregory", "model_name": model_name, "field_name": field_name},
		)
		self.assertEqual(response.status_code, 200)
		return response.json()["results"]

	def test_finds_a_patent_by_publication_number_and_labels_it(self):
		(result,) = self._search(self.editor, "WO2023123456", "patenttriallink", "patent")
		self.assertEqual(result["id"], str(self.patent.pk))
		self.assertEqual(result["text"], patent_label(self.patent))
		self.assertEqual(result["text"], "WO2023123456 · A patent on a remyelination drug")

	def test_does_not_offer_another_organisations_patent(self):
		self.assertEqual(self._search(self.editor, "Another organisation", "patenttriallink", "patent"), [])
		self.assertEqual(len(self._search(self.superuser, "Another organisation", "patenttriallink", "patent")), 1)

	def test_the_trial_picker_on_the_patent_page_uses_the_trial_label(self):
		(result,) = self._search(self.editor, NCT_A, "patenttriallink", "trial")
		self.assertEqual(result["text"], trial_label(self.trial))
		self.assertEqual(self._search(self.editor, "NCT09999999", "patenttriallink", "trial"), [])
