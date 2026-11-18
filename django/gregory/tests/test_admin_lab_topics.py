"""The lab topics in the admin: the subjects and categories sections of the lab page, the
Labs list's subject and category filters, ranking and Topics column. The tables and the
rebuild are tested in test_lab_topics.py."""

import re
from datetime import datetime, timezone as dt_timezone

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from gregory.models import (
	Lab,
	LabCategory,
	LabMembership,
	LabSubject,
	Subject,
	TeamCategory,
)
from gregory.tests.test_admin_labs import LabAdminCase, author, join, make_lab, post_data

LIST = "admin:gregory_lab_changelist"


class TopicCase(LabAdminCase):
	@classmethod
	def setUpTestData(cls):
		super().setUpTestData()
		cls.cat_a = TeamCategory.objects.create(team=cls.team, category_name="Alpha", category_slug="alpha")
		cls.cat_b = TeamCategory.objects.create(team=cls.team, category_name="Beta", category_slug="beta")
		cls.cat_c = TeamCategory.objects.create(team=cls.team, category_name="Gamma", category_slug="gamma")
		cls.cat_d = TeamCategory.objects.create(team=cls.team, category_name="Delta", category_slug="delta")

	def setUp(self):
		self.lab = make_lab(author(1))
		self.login()

	def url(self, lab=None):
		return reverse("admin:gregory_lab_change", args=[(lab or self.lab).pk])

	def formset(self, response, model, hidden):
		for inline in response.context["inline_admin_formsets"]:
			if inline.opts.model is model and inline.opts.hidden is hidden:
				return inline.formset
		raise AssertionError("no such inline")

	def add(self, data, formset, **fields):
		"""Add one new row to ``formset``'s submission."""
		prefix = formset.prefix
		n = int(data[f"{prefix}-TOTAL_FORMS"])
		data[f"{prefix}-TOTAL_FORMS"] = str(n + 1)
		for name, value in fields.items():
			data[f"{prefix}-{n}-{name}"] = value

	def post(self, lab=None, edit=None):
		url = self.url(lab)
		response = self.client.get(url)
		self.assertEqual(response.status_code, 200)
		data = post_data(response)
		if edit:
			edit(response, data)
		return self.client.post(url, data)


class SubjectsAndCategoriesSectionTests(TopicCase):
	def test_the_page_shows_the_four_sections_with_the_hidden_ones_collapsed(self):
		LabSubject.objects.create(lab=self.lab, subject=self.subject, article_count=4, relevant_count=1)
		LabSubject.objects.create(lab=self.lab, subject=self.other_subject, is_hidden=True)
		response = self.client.get(self.url())
		self.assertEqual(response.status_code, 200)
		kinds = [
			(inline.opts.model.__name__, inline.opts.hidden, "collapse" in inline.classes)
			for inline in response.context["inline_admin_formsets"]
			if inline.opts.model is not LabMembership
		]
		self.assertEqual(
			kinds,
			[
				("LabSubject", False, False),
				("LabSubject", True, True),
				("LabCategory", False, False),
				("LabCategory", True, True),
			],
		)
		self.assertContains(response, "Subj S (Labs Team)")

	def test_each_section_holds_its_own_rows_best_first(self):
		LabSubject.objects.create(lab=self.lab, subject=self.subject, article_count=9, relevant_count=1)
		LabSubject.objects.create(lab=self.lab, subject=self.other_subject, article_count=2, relevant_count=2)
		LabSubject.objects.create(
			lab=self.lab, subject=Subject.objects.create(subject_name="Hid", subject_slug="hid", team=self.team),
			is_hidden=True,
		)
		for cat, papers, relevant in ((self.cat_a, 5, 0), (self.cat_b, 7, 0), (self.cat_c, 7, 0)):
			LabCategory.objects.create(lab=self.lab, category=cat, article_count=papers, relevant_count=relevant)
		response = self.client.get(self.url())
		visible = self.formset(response, LabSubject, False)
		self.assertEqual([f.instance.subject_id for f in visible.initial_forms], [self.other_subject.pk, self.subject.pk])
		self.assertEqual(len(self.formset(response, LabSubject, True).initial_forms), 1)
		# Papers, then name, within equal relevant counts.
		cats = self.formset(response, LabCategory, False)
		self.assertEqual(
			[f.instance.category_id for f in cats.initial_forms], [self.cat_b.pk, self.cat_c.pk, self.cat_a.pk]
		)

	def test_adding_a_subject_creates_a_manual_visible_link(self):
		def edit(response, data):
			self.add(data, self.formset(response, LabSubject, False), subject=self.subject.pk)

		self.assertEqual(self.post(edit=edit).status_code, 302)
		link = LabSubject.objects.get(lab=self.lab, subject=self.subject)
		self.assertEqual((link.is_manual, link.is_hidden, link.article_count), (True, False, 0))
		# A normal save, so history records the person's change.
		self.assertEqual(LabSubject.history.filter(lab=self.lab, subject=self.subject).count(), 1)

	def test_adding_a_category_creates_a_manual_visible_link(self):
		def edit(response, data):
			self.add(data, self.formset(response, LabCategory, False), category=self.cat_a.pk)

		self.assertEqual(self.post(edit=edit).status_code, 302)
		link = LabCategory.objects.get(lab=self.lab, category=self.cat_a)
		self.assertEqual((link.is_manual, link.is_hidden), (True, False))

	def test_adding_a_hidden_link_again_unhides_it_and_keeps_its_counts(self):
		LabSubject.objects.create(
			lab=self.lab, subject=self.subject, article_count=5, relevant_count=2, is_hidden=True
		)

		def edit(response, data):
			self.add(data, self.formset(response, LabSubject, False), subject=self.subject.pk)

		self.assertEqual(self.post(edit=edit).status_code, 302)
		link = LabSubject.objects.get(lab=self.lab, subject=self.subject)
		self.assertEqual((link.is_manual, link.is_hidden, link.article_count, link.relevant_count), (True, False, 5, 2))
		self.assertEqual(LabSubject.objects.filter(lab=self.lab).count(), 1)

	def test_adding_a_link_the_lab_already_shows_is_not_an_error(self):
		LabSubject.objects.create(lab=self.lab, subject=self.subject, article_count=5)

		def edit(response, data):
			self.add(data, self.formset(response, LabSubject, False), subject=self.subject.pk)

		self.assertEqual(self.post(edit=edit).status_code, 302)
		link = LabSubject.objects.get(lab=self.lab, subject=self.subject)
		self.assertEqual((link.is_manual, link.article_count), (True, 5))

	def test_hide_and_reset_do_nothing_on_a_new_row(self):
		def edit(response, data):
			self.add(
				data,
				self.formset(response, LabSubject, False),
				subject=self.subject.pk,
				hide="on",
				reset="on",
			)

		self.assertEqual(self.post(edit=edit).status_code, 302)
		link = LabSubject.objects.get(lab=self.lab, subject=self.subject)
		self.assertEqual((link.is_manual, link.is_hidden), (True, False))
		response = self.client.get(self.url())
		empty = self.formset(response, LabSubject, False).empty_form
		self.assertTrue(empty.fields["hide"].disabled)
		self.assertTrue(empty.fields["reset"].disabled)

	def test_a_category_row_offers_no_add_or_edit_of_the_category(self):
		LabCategory.objects.create(lab=self.lab, category=self.cat_a, article_count=3)
		response = self.client.get(self.url())
		widget = self.formset(response, LabCategory, False).forms[0].fields["category"].widget
		self.assertFalse(widget.can_add_related)
		self.assertFalse(widget.can_change_related)
		self.assertFalse(widget.can_delete_related)

	def test_unhiding_a_link_added_in_the_same_save_keeps_it_manual(self):
		LabSubject.objects.create(lab=self.lab, subject=self.subject, is_hidden=True)

		def edit(response, data):
			self.add(data, self.formset(response, LabSubject, False), subject=self.subject.pk)
			data[f"{self.formset(response, LabSubject, True).prefix}-0-unhide"] = "on"

		self.assertEqual(self.post(edit=edit).status_code, 302)
		link = LabSubject.objects.get(lab=self.lab, subject=self.subject)
		self.assertEqual((link.is_manual, link.is_hidden), (True, False))

	def test_the_same_topic_twice_in_one_submission_is_refused(self):
		def edit(response, data):
			formset = self.formset(response, LabCategory, False)
			self.add(data, formset, category=self.cat_a.pk)
			self.add(data, formset, category=self.cat_a.pk)

		response = self.post(edit=edit)
		self.assertEqual(response.status_code, 200)
		self.assertFalse(LabCategory.objects.filter(lab=self.lab).exists())

	def test_hiding_moves_a_link_to_the_hidden_section_and_keeps_its_flags_and_counts(self):
		link = LabCategory.objects.create(lab=self.lab, category=self.cat_a, article_count=4)

		def edit(response, data):
			data[f"{self.formset(response, LabCategory, False).prefix}-0-hide"] = "on"

		self.assertEqual(self.post(edit=edit).status_code, 302)
		link.refresh_from_db()
		self.assertEqual((link.is_hidden, link.is_manual, link.article_count), (True, False, 4))
		response = self.client.get(self.url())
		self.assertEqual(len(self.formset(response, LabCategory, False).initial_forms), 0)
		self.assertEqual(len(self.formset(response, LabCategory, True).initial_forms), 1)
		self.assertEqual(LabCategory.history.filter(lab=self.lab, is_hidden=True).count(), 1)

	def test_unhiding_restores_the_link(self):
		link = LabCategory.objects.create(lab=self.lab, category=self.cat_a, is_hidden=True)

		def edit(response, data):
			data[f"{self.formset(response, LabCategory, True).prefix}-0-unhide"] = "on"

		self.assertEqual(self.post(edit=edit).status_code, 302)
		link.refresh_from_db()
		self.assertEqual((link.is_hidden, link.is_manual), (False, False))

	def test_reset_clears_both_flags(self):
		link = LabSubject.objects.create(lab=self.lab, subject=self.subject, is_manual=True)
		hidden = LabCategory.objects.create(lab=self.lab, category=self.cat_a, is_manual=True, is_hidden=True)

		def edit(response, data):
			data[f"{self.formset(response, LabSubject, False).prefix}-0-reset"] = "on"
			data[f"{self.formset(response, LabCategory, True).prefix}-0-reset"] = "on"

		self.assertEqual(self.post(edit=edit).status_code, 302)
		link.refresh_from_db()
		hidden.refresh_from_db()
		self.assertEqual((link.is_manual, link.is_hidden), (False, False))
		self.assertEqual((hidden.is_manual, hidden.is_hidden), (False, False))

	def test_an_untouched_save_writes_no_history_and_never_changes_counts(self):
		LabSubject.objects.create(lab=self.lab, subject=self.subject, article_count=4, relevant_count=3)
		before = LabSubject.history.count()
		self.assertEqual(self.post().status_code, 302)
		self.assertEqual(LabSubject.history.count(), before)
		self.assertEqual(LabSubject.objects.get().relevant_count, 3)

	def test_the_topic_of_an_existing_row_cannot_be_changed(self):
		link = LabSubject.objects.create(lab=self.lab, subject=self.subject)

		def edit(response, data):
			data[f"{self.formset(response, LabSubject, False).prefix}-0-subject"] = self.other_subject.pk

		self.post(edit=edit)
		link.refresh_from_db()
		self.assertEqual(link.subject_id, self.subject.pk)

	def test_a_member_row_still_pins_the_author_on_delete(self):
		member = author(2)
		join(self.lab, member)
		LabSubject.objects.create(lab=self.lab, subject=self.subject)

		def edit(response, data):
			data["memberships-0-DELETE"] = "on"

		self.assertEqual(self.post(edit=edit).status_code, 302)
		membership = self.lab_membership(member)
		self.assertEqual(membership.lab, None)
		self.assertEqual(LabSubject.objects.filter(lab=self.lab).count(), 1)

	def test_topic_counts_last_changed_shows_the_latest_computed_at(self):
		self.assertContains(self.client.get(self.url()), "Topic counts last changed")
		LabSubject.objects.create(
			lab=self.lab, subject=self.subject, computed_at=datetime(2026, 3, 1, 12, 0, tzinfo=dt_timezone.utc)
		)
		LabCategory.objects.create(
			lab=self.lab, category=self.cat_a, computed_at=datetime(2026, 5, 20, 9, 30, tzinfo=dt_timezone.utc)
		)
		response = self.client.get(self.url())
		self.assertContains(response, "May 20, 2026")
		self.assertNotContains(response, "March 1, 2026")

	def test_non_superusers_are_refused_the_lab_page(self):
		self.login(self.staff)
		self.assertEqual(self.client.get(self.url()).status_code, 403)
		self.assertEqual(self.client.post(self.url(), {}).status_code, 403)


class LabListTopicTests(TopicCase):
	def setUp(self):
		super().setUp()
		self.lab_b = make_lab(author(10))
		self.lab_c = make_lab(author(20))
		# Subject S: lab (2 relevant), lab_b (5 relevant), lab_c hidden.
		LabSubject.objects.create(
			lab=self.lab, subject=self.subject, article_count=9, relevant_count=2, recent_article_count=1
		)
		LabSubject.objects.create(
			lab=self.lab_b, subject=self.subject, article_count=5, relevant_count=5, recent_article_count=2
		)
		LabSubject.objects.create(
			lab=self.lab_c, subject=self.subject, article_count=50, relevant_count=50, is_hidden=True
		)
		# Category Alpha: lab, lab_b (more papers, same relevant), lab_c hidden. Beta: lab only.
		LabCategory.objects.create(lab=self.lab, category=self.cat_a, article_count=3, relevant_count=1)
		LabCategory.objects.create(lab=self.lab_b, category=self.cat_a, article_count=8, relevant_count=1)
		LabCategory.objects.create(lab=self.lab_c, category=self.cat_a, article_count=9, relevant_count=9, is_hidden=True)
		LabCategory.objects.create(lab=self.lab, category=self.cat_b, article_count=4, relevant_count=0)

	def rows(self, **params):
		return list(self.client.get(reverse(LIST), params).context["cl"].result_list)

	def test_the_subject_filter_narrows_to_visible_links_and_ranks_by_relevant_then_papers(self):
		labs = self.rows(subject=self.subject.pk)
		self.assertEqual([l.pk for l in labs], [self.lab_b.pk, self.lab.pk])
		self.assertEqual(
			[(l.articles_in_subject, l.relevant_in_subject, l.recent_in_subject) for l in labs],
			[(5, 5, 2), (9, 2, 1)],
		)

	def test_the_category_filter_narrows_and_ranks(self):
		labs = self.rows(category=self.cat_a.pk)
		self.assertEqual([l.pk for l in labs], [self.lab_b.pk, self.lab.pk])
		self.assertEqual(
			[(l.articles_in_category, l.relevant_in_category, l.recent_in_category) for l in labs],
			[(8, 1, 0), (3, 1, 0)],
		)
		self.assertEqual([l.pk for l in self.rows(category=self.cat_b.pk)], [self.lab.pk])
		self.assertEqual(self.rows(category=self.cat_d.pk), [])

	def test_both_filters_together_keep_labs_with_both_links_ranked_by_the_category(self):
		labs = self.rows(subject=self.subject.pk, category=self.cat_a.pk)
		self.assertEqual([l.pk for l in labs], [self.lab_b.pk, self.lab.pk])
		self.assertEqual((labs[0].articles_in_subject, labs[0].articles_in_category), (5, 8))
		response = self.client.get(reverse(LIST), {"subject": self.subject.pk, "category": self.cat_a.pk})
		for header in ("Relevant in subject", "Relevant in category", "Recent in category"):
			self.assertContains(response, header)
		# Lab B has no link to Beta.
		self.assertEqual(
			[l.pk for l in self.rows(subject=self.subject.pk, category=self.cat_b.pk)], [self.lab.pk]
		)

	def test_the_category_ranking_wins_when_it_disagrees_with_the_subject(self):
		LabCategory.objects.filter(lab=self.lab, category=self.cat_a).update(relevant_count=7)
		labs = self.rows(subject=self.subject.pk, category=self.cat_a.pk)
		self.assertEqual([l.pk for l in labs], [self.lab.pk, self.lab_b.pk])

	def test_hidden_links_are_absent_from_filters_and_lookups(self):
		self.assertNotIn(self.lab_c.pk, [l.pk for l in self.rows(subject=self.subject.pk)])
		self.assertNotIn(self.lab_c.pk, [l.pk for l in self.rows(category=self.cat_a.pk)])
		response = self.client.get(reverse(LIST))
		spec = [f for f in response.context["cl"].filter_specs if getattr(f, "parameter_name", None) == "category"][0]
		# Alpha and Beta have a visible link; Gamma has none, and a category with only a hidden link wouldn't count.
		self.assertEqual([label for _, label in spec.lookup_choices], ["Alpha (Labs Team)", "Beta (Labs Team)"])
		LabCategory.objects.filter(category=self.cat_b).update(is_hidden=True)
		spec = [f for f in self.client.get(reverse(LIST)).context["cl"].filter_specs if getattr(f, "parameter_name", None) == "category"][0]
		self.assertEqual([label for _, label in spec.lookup_choices], ["Alpha (Labs Team)"])

	def test_a_chosen_category_with_no_visible_link_stays_in_the_filter(self):
		LabCategory.objects.create(lab=self.lab, category=self.cat_d, is_hidden=True)
		response = self.client.get(reverse(LIST), {"category": self.cat_d.pk})
		self.assertEqual(response.context["cl"].result_count, 0)
		self.assertContains(response, "Delta (Labs Team)")
		self.assertNotContains(self.client.get(reverse(LIST)), "Delta (Labs Team)")

	def test_the_category_filter_is_a_searchable_list_that_keeps_other_filters(self):
		response = self.client.get(reverse(LIST), {"country": "GB"})
		self.assertContains(response, "lab-category-search")
		html = response.content.decode()
		links = re.findall(r'<a href="(\?[^"]*category=%d[^"]*)"' % self.cat_a.pk, html)
		self.assertTrue(links)
		self.assertIn("country=GB", links[0])

	def test_sorting_by_a_topic_column(self):
		url = reverse(LIST)
		response = self.client.get(url, {"category": self.cat_a.pk})
		columns = [c for c in response.context["cl"].list_display]
		self.assertIn("articles_in_category", columns)
		position = columns.index("articles_in_category")
		ascending = self.client.get(url, {"category": self.cat_a.pk, "o": str(position)})
		self.assertEqual([l.pk for l in ascending.context["cl"].result_list], [self.lab.pk, self.lab_b.pk])

	def test_the_topics_column_shows_the_top_three_visible_categories(self):
		LabCategory.objects.create(lab=self.lab, category=self.cat_c, article_count=3, relevant_count=0)
		LabCategory.objects.create(lab=self.lab, category=self.cat_d, article_count=9, relevant_count=0)
		LabCategory.objects.create(lab=self.lab, category=TeamCategory.objects.create(
			team=self.team, category_name="Epsilon", category_slug="epsilon"), article_count=3, is_hidden=True,
			relevant_count=9,
		)
		by_pk = {l.pk: l for l in self.rows()}
		admin_obj = self.client.get(reverse(LIST)).context["cl"].model_admin
		# Alpha (1 relevant) first, then Delta (9 papers), then Beta (4 papers); Gamma and hidden Epsilon fall out.
		self.assertEqual(admin_obj.topics(by_pk[self.lab.pk]), "Alpha, Delta, Beta")
		self.assertEqual(admin_obj.topics(by_pk[self.lab_c.pk]), "-")
		self.assertContains(self.client.get(reverse(LIST)), "Alpha, Delta, Beta")

	def test_a_category_name_is_escaped_in_the_topics_column(self):
		TeamCategory.objects.filter(pk=self.cat_b.pk).update(category_name="<script>x</script>")
		response = self.client.get(reverse(LIST))
		self.assertNotContains(response, "<script>x</script>")
		self.assertContains(response, "&lt;script&gt;x&lt;/script&gt;")

	def test_an_unknown_or_bad_topic_value_shows_no_labs_or_is_ignored(self):
		self.assertEqual(self.rows(subject=999999), [])
		self.assertEqual(len(self.rows(category="abc")), 3)

	def test_a_non_superuser_gets_403_on_the_list(self):
		self.login(self.staff)
		self.assertEqual(self.client.get(reverse(LIST), {"category": self.cat_a.pk}).status_code, 403)

	def test_the_list_runs_the_same_queries_however_many_labs_there_are(self):
		def queries(**params):
			with CaptureQueriesContext(connection) as ctx:
				self.assertEqual(self.client.get(reverse(LIST), params).status_code, 200)
			return len(ctx)

		def counts():
			return (
				queries(),
				queries(subject=self.subject.pk),
				queries(category=self.cat_a.pk),
				queries(subject=self.subject.pk, category=self.cat_a.pk),
			)

		counts()  # warm the content type and permission caches
		small = counts()
		for n in range(30, 38):
			lab = make_lab(author(n))
			LabSubject.objects.create(lab=lab, subject=self.subject, article_count=3)
			for cat in (self.cat_a, self.cat_b, self.cat_c, self.cat_d):
				LabCategory.objects.create(lab=lab, category=cat, article_count=3)
		self.assertEqual(Lab.objects.count(), 11)
		self.assertEqual(counts(), small)
