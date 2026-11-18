"""Tests for the links between labs and subjects or categories: the counting, the rebuild's
write rules, the rebuild_lab_topics command and what a lab merge carries over."""

from datetime import date, datetime, timezone as dt_timezone
from io import StringIO
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import RequestFactory, SimpleTestCase, TestCase
from organizations.models import Organization

from gregory.models import (
	Articles,
	ArticleSubjectRelevance,
	Authors,
	Lab,
	LabCategory,
	LabMembership,
	LabSubject,
	MLPredictions,
	Subject,
	Team,
	TeamCategory,
)
from gregory.services import lab_topics
from gregory.services.lab_topics import (
	LabTopicRebuildAborted,
	TopicCounts,
	TopicLinksResult,
	apply_topic_links,
	compute_topic_links,
	count_topic,
)
from gregory.services.lab_grouping import LabsResult
from gregory.services.labs import apply_labs, merge_labs

TODAY = date(2026, 6, 1)
HEAD = LabMembership.Role.HEAD
MEMBER = LabMembership.Role.MEMBER
COLLABORATOR = LabMembership.Role.COLLABORATOR


def counts(articles=3, relevant=0, recent=0, year=None):
	return TopicCounts(articles, relevant, recent, year)


class LabTopicsBase(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.org = Organization.objects.create(name="Org", slug="lab-topics-org")
		cls.team = Team.objects.create(name="Team", slug="lab-topics-team", organization=cls.org)
		cls.subject = cls.make_subject("one")
		cls.other_subject = cls.make_subject("two")
		cls.category = cls.make_category("cat", cls.subject)
		cls.head = cls.make_author(1)
		cls.member = cls.make_author(2)
		cls.lab = cls.make_lab(cls.head)
		LabMembership.objects.create(author=cls.member, lab=cls.lab, role=MEMBER)
		cls.paper_count = 0

	@classmethod
	def make_subject(cls, slug):
		return Subject.objects.create(
			subject_name=slug,
			subject_slug=f"lab-topics-{slug}",
			team=cls.team,
			auto_predict=True,
			ml_consensus_type="any",
		)

	@classmethod
	def make_category(cls, slug, *subjects):
		category = TeamCategory.objects.create(
			team=cls.team, category_name=slug, category_slug=f"lab-topics-{slug}"
		)
		category.subjects.add(*subjects)
		return category

	@staticmethod
	def make_author(n):
		return Authors.objects.create(given_name=f"Given{n}", family_name=f"Family{n}")

	@staticmethod
	def make_lab(head):
		lab = Lab.objects.create(head=head)
		LabMembership.objects.create(author=head, lab=lab, role=HEAD)
		return lab

	def paper(self, *authors, subjects=None, categories=None, year=2020):
		type(self).paper_count += 1
		article = Articles.objects.create(
			title=f"Paper {self.paper_count}",
			link=f"https://example.com/lab-topics/{self.paper_count}",
			published_date=datetime(year, 6, 1, tzinfo=dt_timezone.utc) if year else None,
		)
		article.authors.add(*(authors or [self.head]))
		article.subjects.add(*(self.subject,) if subjects is None else subjects)
		article.categories.add(*(self.category,) if categories is None else categories)
		return article

	def papers(self, number, *authors, **kwargs):
		return [self.paper(*authors, **kwargs) for _ in range(number)]

	def rebuild(self, **kwargs):
		return apply_topic_links(compute_topic_links(TODAY), **kwargs)

	def ml_yes(self, article, subject):
		MLPredictions.objects.create(
			article=article,
			subject=subject,
			algorithm="pubmed_bert",
			probability_score=0.95,
			predicted_relevant=True,
		)


class CountTopicTests(SimpleTestCase):
	def test_counts_each_paper_once_per_lab(self):
		result = count_topic(
			{1, 2, 3},
			{2},
			{1: {10}, 2: {10, 20}, 3: {20}},
			{1: 2020, 2: 2024},
			2022,
		)
		self.assertEqual(result[10], TopicCounts(2, 1, 1, 2024))
		self.assertEqual(result[20], TopicCounts(2, 1, 1, 2024))

	def test_a_paper_with_no_lab_counts_for_nobody(self):
		self.assertEqual(count_topic({1}, {1}, {}, {1: 2020}, 2022), {})


class ComputeTests(LabTopicsBase):
	def test_collaborators_and_pinned_authors_add_nothing(self):
		collaborator, pinned = self.make_author(3), self.make_author(4)
		LabMembership.objects.create(author=collaborator, lab=self.lab, role=COLLABORATOR)
		LabMembership.objects.create(author=pinned, lab=None, source="manual")
		self.papers(2, collaborator)
		self.papers(2, pinned)
		self.assertEqual(compute_topic_links(TODAY).subjects, {})

	def test_two_people_of_one_lab_on_a_paper_count_once(self):
		self.paper(self.head, self.member)
		found = compute_topic_links(TODAY)
		self.assertEqual(found.subjects[(self.lab.pk, self.subject.pk)].article_count, 1)
		self.assertEqual(found.categories[(self.lab.pk, self.category.pk)].article_count, 1)

	def test_members_papers_count(self):
		self.paper(self.member)
		found = compute_topic_links(TODAY)
		self.assertIn((self.lab.pk, self.subject.pk), found.subjects)

	def test_recent_window_and_undated_papers(self):
		self.paper(year=2022)
		self.paper(year=2021)
		self.paper(year=None)
		link = compute_topic_links(TODAY).subjects[(self.lab.pk, self.subject.pk)]
		self.assertEqual(link, TopicCounts(3, 0, 1, 2022))

	def test_relevant_by_an_editor(self):
		article = self.paper()
		ArticleSubjectRelevance.objects.create(
			article=article, subject=self.subject, is_relevant=True
		)
		link = compute_topic_links(TODAY).subjects[(self.lab.pk, self.subject.pk)]
		self.assertEqual(link.relevant_count, 1)

	def test_ml_consensus_counts_even_when_an_editor_said_no(self):
		article = self.paper()
		self.ml_yes(article, self.subject)
		ArticleSubjectRelevance.objects.create(
			article=article, subject=self.subject, is_relevant=False
		)
		link = compute_topic_links(TODAY).subjects[(self.lab.pk, self.subject.pk)]
		self.assertEqual(link.relevant_count, 1)

	def test_a_verdict_for_another_subject_does_not_count(self):
		article = self.paper(subjects=[self.subject, self.other_subject])
		ArticleSubjectRelevance.objects.create(
			article=article, subject=self.other_subject, is_relevant=True
		)
		found = compute_topic_links(TODAY).subjects
		self.assertEqual(found[(self.lab.pk, self.subject.pk)].relevant_count, 0)
		self.assertEqual(found[(self.lab.pk, self.other_subject.pk)].relevant_count, 1)

	def test_a_category_paper_is_relevant_through_any_of_its_subjects(self):
		category = self.make_category("both", self.subject, self.other_subject)
		article = self.paper(subjects=[self.other_subject], categories=[category])
		self.ml_yes(article, self.other_subject)
		link = compute_topic_links(TODAY).categories[(self.lab.pk, category.pk)]
		self.assertEqual(link.relevant_count, 1)

	def test_a_category_paper_relevant_only_outside_the_category_does_not_count(self):
		article = self.paper(subjects=[self.subject, self.other_subject])
		self.ml_yes(article, self.other_subject)
		link = compute_topic_links(TODAY).categories[(self.lab.pk, self.category.pk)]
		self.assertEqual(link.relevant_count, 0)


class ApplyTests(LabTopicsBase):
	def link(self, **kwargs):
		return LabSubject.objects.create(lab=self.lab, subject=self.subject, **kwargs)

	def test_a_link_appears_at_three_papers_and_not_at_two(self):
		self.papers(2)
		self.rebuild()
		self.assertFalse(LabSubject.objects.exists())
		self.assertFalse(LabCategory.objects.exists())
		self.paper()
		stats = self.rebuild()
		self.assertEqual((stats.subjects.created, stats.categories.created), (1, 1))
		link = LabSubject.objects.get()
		self.assertEqual((link.lab, link.subject, link.article_count), (self.lab, self.subject, 3))
		self.assertIsNotNone(link.computed_at)
		self.assertEqual(LabCategory.objects.get().category, self.category)

	def test_a_link_goes_when_the_count_drops(self):
		self.link(article_count=3)
		other = self.make_subject("kept")
		LabSubject.objects.create(lab=self.lab, subject=other, article_count=3)
		stats = apply_topic_links(
			TopicLinksResult(subjects={(self.lab.pk, other.pk): counts(3)}, categories={}),
			force=True,
		)
		self.assertEqual(stats.subjects.deleted, 1)
		self.assertEqual(LabSubject.objects.get().subject, other)

	def test_added_and_hidden_links_stay_with_current_counts(self):
		manual = self.link(is_manual=True, article_count=9, latest_article_year=2020)
		category = LabCategory.objects.create(
			lab=self.lab, category=self.category, is_hidden=True, article_count=9
		)
		stats = apply_topic_links(
			TopicLinksResult(
				subjects={},
				categories={(self.lab.pk, self.category.pk): counts(2, 1, 1, 2025)},
			)
		)
		manual.refresh_from_db()
		category.refresh_from_db()
		self.assertTrue(manual.is_manual)
		self.assertEqual((manual.article_count, manual.latest_article_year), (0, None))
		self.assertTrue(category.is_hidden)
		self.assertEqual(category.article_count, 2)
		self.assertEqual(category.latest_article_year, 2025)
		self.assertEqual(stats.subjects.manual_kept, 1)
		self.assertEqual(stats.categories.hidden_kept, 1)

	def test_a_second_run_writes_nothing(self):
		self.papers(3)
		self.rebuild(now=datetime(2026, 1, 1, tzinfo=dt_timezone.utc))
		before = LabSubject.objects.get().computed_at
		stats = self.rebuild(now=datetime(2026, 2, 1, tzinfo=dt_timezone.utc))
		self.assertEqual(LabSubject.objects.get().computed_at, before)
		for model_stats in (stats.subjects, stats.categories):
			self.assertEqual(
				(model_stats.created, model_stats.updated, model_stats.deleted), (0, 0, 0)
			)

	def test_computed_at_moves_only_when_counts_change(self):
		self.papers(3)
		self.rebuild(now=datetime(2026, 1, 1, tzinfo=dt_timezone.utc))
		self.paper()
		later = datetime(2026, 2, 1, tzinfo=dt_timezone.utc)
		self.assertEqual(self.rebuild(now=later).subjects.updated, 1)
		self.assertEqual(LabSubject.objects.get().computed_at, later)

	def test_dry_run_writes_nothing_and_reports_the_changes(self):
		self.papers(3)
		stats = self.rebuild(dry_run=True)
		self.assertEqual(stats.subjects.created, 1)
		self.assertFalse(LabSubject.objects.exists())

	def test_safety_stop_covers_both_tables_before_any_write(self):
		LabCategory.objects.create(lab=self.lab, category=self.category, article_count=3)
		new = TopicLinksResult(subjects={(self.lab.pk, self.subject.pk): counts(3)})
		with self.assertRaises(LabTopicRebuildAborted):
			apply_topic_links(new)
		self.assertFalse(LabSubject.objects.exists())
		self.assertTrue(LabCategory.objects.exists())
		apply_topic_links(new, force=True)
		self.assertFalse(LabCategory.objects.exists())

	def test_flagged_links_do_not_count_towards_the_safety_stop(self):
		self.link(is_hidden=True)
		apply_topic_links(TopicLinksResult())
		self.assertTrue(LabSubject.objects.exists())

	def test_half_exactly_does_not_stop(self):
		other = self.make_subject("other")
		self.link(article_count=3)
		LabSubject.objects.create(lab=self.lab, subject=other, article_count=3)
		stats = apply_topic_links(
			TopicLinksResult(subjects={(self.lab.pk, other.pk): counts(3)})
		)
		self.assertEqual(stats.subjects.deleted, 1)

	def test_a_flag_set_during_the_run_survives_a_delete(self):
		link = self.link(article_count=3)
		other = self.make_subject("other")
		LabSubject.objects.create(lab=self.lab, subject=other, article_count=3)
		stored = list(LabSubject.objects.all())

		def read_then_hide(model):
			if model is LabSubject:
				LabSubject.objects.filter(pk=link.pk).update(is_hidden=True)
				return stored
			return []

		with patch.object(lab_topics, "_stored_links", read_then_hide):
			stats = apply_topic_links(
				TopicLinksResult(subjects={(self.lab.pk, other.pk): counts(3)})
			)
		link.refresh_from_db()
		self.assertTrue(link.is_hidden)
		self.assertEqual(stats.subjects.deleted, 0)

	def test_a_flag_set_during_the_run_survives_a_count_update(self):
		link = self.link(article_count=3)
		stored = list(LabSubject.objects.all())

		def read_then_hide(model):
			if model is LabSubject:
				LabSubject.objects.filter(pk=link.pk).update(is_hidden=True)
				return stored
			return []

		with patch.object(lab_topics, "_stored_links", read_then_hide):
			apply_topic_links(
				TopicLinksResult(subjects={(self.lab.pk, self.subject.pk): counts(5)})
			)
		link.refresh_from_db()
		self.assertTrue(link.is_hidden)
		self.assertEqual(link.article_count, 5)

	def test_a_link_added_during_the_run_is_not_a_conflict(self):
		def read_then_add(model):
			if model is LabSubject:
				LabSubject.objects.create(lab=self.lab, subject=self.subject, is_manual=True)
			return []

		with patch.object(lab_topics, "_stored_links", read_then_add):
			apply_topic_links(
				TopicLinksResult(subjects={(self.lab.pk, self.subject.pk): counts(5)})
			)
		self.assertTrue(LabSubject.objects.get().is_manual)

	def test_the_rebuild_adds_no_history(self):
		self.papers(3)
		self.rebuild()
		self.paper()
		self.rebuild()
		apply_topic_links(TopicLinksResult(), force=True)
		self.assertFalse(LabSubject.objects.exists())
		self.assertEqual(LabSubject.history.count(), 0)
		self.assertEqual(LabCategory.history.count(), 0)

	def test_a_persons_change_is_in_the_history(self):
		link = self.link()
		link.is_hidden = True
		link.save()
		self.assertEqual(link.history.count(), 2)

	def test_the_rebuild_leaves_articles_alone(self):
		article = self.papers(3)[0]
		self.ml_yes(article, self.subject)
		ArticleSubjectRelevance.objects.create(
			article=article, subject=self.subject, is_relevant=False
		)
		def snapshot():
			return (
				list(Articles.objects.order_by("pk").values_list("pk", "relevant", "last_updated")),
				list(ArticleSubjectRelevance.objects.values_list("pk", "is_relevant")),
				MLPredictions.objects.count(),
				list(Articles.categories.through.objects.order_by("pk").values_list("pk", flat=True)),
			)

		before = snapshot()
		self.rebuild()
		self.assertTrue(LabSubject.objects.exists())
		self.assertEqual(snapshot(), before)

	def test_one_link_per_lab_and_topic(self):
		self.link()
		with self.assertRaises(IntegrityError), transaction.atomic():
			self.link()


class CommandTests(LabTopicsBase):
	def run_command(self, *args):
		out = StringIO()
		call_command("rebuild_lab_topics", *args, stdout=out)
		return out.getvalue()

	def test_rebuilds(self):
		self.papers(3)
		output = self.run_command()
		self.assertIn("subject links 1 created", output)
		self.assertEqual(LabSubject.objects.count(), 1)
		self.assertEqual(LabCategory.objects.count(), 1)

	def test_dry_run(self):
		self.papers(3)
		self.assertIn("Would change: subject links 1 created", self.run_command("--dry-run"))
		self.assertFalse(LabSubject.objects.exists())

	def test_the_safety_stop_is_a_command_error_and_force_overrides_it(self):
		LabSubject.objects.create(lab=self.lab, subject=self.subject, article_count=3)
		with self.assertRaisesMessage(CommandError, "--force"):
			self.run_command()
		self.assertTrue(LabSubject.objects.exists())
		self.run_command("--force")
		self.assertFalse(LabSubject.objects.exists())

	def test_stops_quietly_when_another_run_holds_the_lock(self):
		self.papers(3)
		with patch(
			"gregory.management.commands.rebuild_lab_topics.fcntl.flock",
			side_effect=BlockingIOError,
		):
			self.assertIn("in progress", self.run_command())
		self.assertFalse(LabSubject.objects.exists())

	@patch("gregory.management.commands.pipeline.call_command")
	def test_the_pipeline_runs_it_after_relevance_is_refreshed(self, mock_call):
		call_command("pipeline", stdout=StringIO(), stderr=StringIO())
		called = [c.args[0] for c in mock_call.call_args_list]
		self.assertEqual(called.count("rebuild_lab_topics"), 1)
		self.assertLess(called.index("rebuild_labs"), called.index("rebuild_lab_topics"))
		self.assertLess(called.index("rebuild_categories"), called.index("rebuild_lab_topics"))
		self.assertLess(called.index("predict_articles"), called.index("rebuild_lab_topics"))
		self.assertEqual(
			called.index("rebuild_lab_topics"), called.index("refresh_article_relevance") + 1
		)

	@patch("gregory.management.commands.pipeline.call_command")
	def test_a_failure_does_not_stop_the_pipeline(self, mock_call):
		def fail_on_topics(name, *args, **kwargs):
			if name == "rebuild_lab_topics":
				raise RuntimeError("boom")

		mock_call.side_effect = fail_on_topics
		err = StringIO()
		call_command("pipeline", stdout=StringIO(), stderr=err)
		self.assertIn("Error running rebuild_lab_topics", err.getvalue())
		self.assertIn("detect_trial_references", [c.args[0] for c in mock_call.call_args_list])

	def test_no_role_gets_permissions_on_the_links(self):
		call_command("setup_roles", stdout=StringIO())
		self.assertFalse(
			Permission.objects.filter(
				group__isnull=False,
				content_type__app_label="gregory",
				content_type__model__in=[
					"labsubject",
					"labcategory",
					"historicallabsubject",
					"historicallabcategory",
				],
			).exists()
		)


class MergeLabsTests(LabTopicsBase):
	def setUp(self):
		self.other = self.make_lab(self.make_author(9))

	def test_added_and_hidden_links_move_to_the_surviving_lab(self):
		LabSubject.objects.create(lab=self.other, subject=self.subject, is_manual=True)
		LabCategory.objects.create(lab=self.other, category=self.category, is_hidden=True)
		merge_labs(self.lab, [self.other])
		self.assertTrue(LabSubject.objects.get(lab=self.lab, subject=self.subject).is_manual)
		self.assertTrue(LabCategory.objects.get(lab=self.lab, category=self.category).is_hidden)
		self.assertEqual(LabSubject.objects.count(), 1)

	def test_hidden_wins_over_added(self):
		LabSubject.objects.create(lab=self.lab, subject=self.subject, is_manual=True)
		LabSubject.objects.create(lab=self.other, subject=self.subject, is_hidden=True)
		merge_labs(self.lab, [self.other])
		link = LabSubject.objects.get()
		self.assertEqual((link.lab, link.is_manual, link.is_hidden), (self.lab, True, True))

	def test_a_hidden_link_on_the_survivor_stays_hidden(self):
		LabSubject.objects.create(lab=self.lab, subject=self.subject, is_hidden=True)
		LabSubject.objects.create(lab=self.other, subject=self.subject, is_manual=True)
		merge_labs(self.lab, [self.other])
		self.assertTrue(LabSubject.objects.get().is_hidden)

	def test_the_survivors_counts_are_kept(self):
		LabSubject.objects.create(lab=self.lab, subject=self.subject, article_count=7)
		LabSubject.objects.create(lab=self.other, subject=self.subject, is_hidden=True)
		merge_labs(self.lab, [self.other])
		self.assertEqual(LabSubject.objects.get().article_count, 7)

	def test_computed_links_go_with_the_removed_lab(self):
		LabSubject.objects.create(lab=self.other, subject=self.subject, article_count=4)
		merge_labs(self.lab, [self.other])
		self.assertFalse(LabSubject.objects.exists())

	def test_only_links_people_touched_leave_a_history_row(self):
		LabSubject.objects.create(lab=self.other, subject=self.subject, article_count=4)
		LabCategory.objects.create(lab=self.other, category=self.category, is_hidden=True)
		before = LabCategory.history.count()
		merge_labs(self.lab, [self.other])
		self.assertEqual(LabSubject.history.filter(history_type="-").count(), 0)
		self.assertEqual(LabCategory.history.filter(history_type="-").count(), 1)
		self.assertGreater(LabCategory.history.count(), before)

	def test_a_rebuild_removing_a_lab_records_only_flagged_links(self):
		LabMembership.objects.filter(lab=self.other).delete()
		LabSubject.objects.create(lab=self.other, subject=self.subject, article_count=4)
		LabCategory.objects.create(lab=self.other, category=self.category, is_manual=True)
		apply_labs(LabsResult(), force=True)
		self.assertFalse(Lab.objects.filter(pk=self.other.pk).exists())
		self.assertFalse(LabSubject.objects.exists())
		self.assertFalse(LabCategory.objects.exists())
		self.assertEqual(LabSubject.history.filter(history_type="-").count(), 0)
		self.assertEqual(LabCategory.history.filter(history_type="-").count(), 1)

	def test_deleting_a_lab_removes_its_links(self):
		LabSubject.objects.create(lab=self.other, subject=self.subject, is_manual=True)
		self.other.delete()
		self.assertFalse(LabSubject.objects.exists())


class CategoryDeletePageTests(LabTopicsBase):
	def deleted_objects(self, **user_flags):
		user = get_user_model().objects.create_user(
			username=f"user{len(user_flags)}", password="x", is_staff=True, **user_flags
		)
		request = RequestFactory().post("/")
		request.user = user
		model_admin = admin.site._registry[TeamCategory]
		return model_admin.get_deleted_objects([self.category], request)

	def setUp(self):
		LabCategory.objects.create(lab=self.lab, category=self.category, article_count=3)

	def test_an_editor_sees_no_lab_links(self):
		to_delete, model_count, _perms, _protected = self.deleted_objects()
		self.assertNotIn("Lab category", str(to_delete))
		self.assertNotIn("lab categories", model_count)
		self.assertIn("Team - cat", str(to_delete))

	def test_a_superuser_still_sees_them(self):
		to_delete, model_count, _perms, _protected = self.deleted_objects(is_superuser=True)
		self.assertIn("Lab category", str(to_delete))
		self.assertEqual(model_count["lab categories"], 1)
