"""Tests for gregory/services/patent_trial_links.py: the rules for a link an editor adds
by hand (describe_link, link_patent_trial, unlink_patent_trial, patent_label) and how
detection leaves those links alone."""

import datetime

from django.contrib.auth import get_user_model

from gregory.models import (
	PatentCategoryAssignment,
	PatentTrialLink,
	Sponsor,
	TrialCategoryAssignment,
	Trials,
)
from gregory.services.patent_trial_links import (
	describe_link,
	link_patent_trial,
	patent_label,
	shared_sponsor_id,
	unlink_patent_trial,
	visible_link,
)
from gregory.tests.test_patent_trial_links import LinkTestCase

User = get_user_model()
D = datetime.date


class DescribeLinkTests(LinkTestCase):
	"""Trial 31834 (NCT07826689, recruiting, started 2026-10-01) against Progentos's
	patents 2335 (priority 2023-05-09) and 2299 (2022-10-05)."""

	def open_trial(self, **kwargs):
		return self.make_trial(status="Recruiting", start=D(2026, 10, 1), end=None, **kwargs)

	def test_before_counts_the_days_to_the_trial_start(self):
		trial = self.open_trial()
		values = describe_link(self.make_patent(D(2023, 5, 9)), trial)
		self.assertEqual(values["timing"], "before")
		self.assertEqual(values["days_before_start"], 1241)
		self.assertIsNone(values["days_after_completion"])
		self.assertEqual(describe_link(self.make_patent(D(2022, 10, 5)), trial)["days_before_start"], 1457)

	def test_during_on_an_open_window(self):
		trial = self.open_trial()
		for priority in (D(2026, 10, 1), D(2040, 1, 1)):
			values = describe_link(self.make_patent(priority), trial)
			self.assertEqual(values["timing"], "during")
			self.assertIsNone(values["days_before_start"])
			self.assertIsNone(values["days_after_completion"])

	def test_during_includes_the_end_of_a_closed_window(self):
		trial = self.make_trial()  # 2020-01-01 to 2022-06-30
		self.assertEqual(describe_link(self.make_patent(D(2022, 6, 30)), trial)["timing"], "during")

	def test_after_has_no_24_month_limit(self):
		trial = self.make_trial()  # ends 2022-06-30
		patent = self.make_patent(D(2025, 1, 1))  # 30 months later: detection would not link it
		values = describe_link(patent, trial)
		self.assertEqual(values["timing"], "after")
		self.assertEqual(values["days_after_completion"], (D(2025, 1, 1) - D(2022, 6, 30)).days)
		self.assertIsNone(values["days_before_start"])

	def test_unknown_window_gives_no_timing(self):
		for trial in (self.make_trial(end=None), self.make_trial(start=None), self.make_trial(status="Withdrawn")):
			values = describe_link(self.make_patent(), trial)
			self.assertIsNone(values["timing"])
			self.assertIsNone(values["days_after_completion"])
			self.assertIsNone(values["days_before_start"])

	def test_a_patent_without_a_priority_date_gives_no_timing(self):
		self.assertIsNone(describe_link(self.make_patent(priority=None), self.make_trial())["timing"])

	def test_shared_sponsor(self):
		trial = self.make_trial()
		patent = self.make_patent()
		self.assertEqual(shared_sponsor_id(patent, trial), self.sponsor.pk)
		self.assertEqual(describe_link(patent, trial)["sponsor_id"], self.sponsor.pk)

	def test_no_shared_sponsor(self):
		# The Progentos case: the trial and the patent point at different sponsor rows.
		other = Sponsor.objects.create(name="ACME NEURO INC", slug="acme-neuro-inc")
		trial = self.make_trial()
		patent = self.make_patent(sponsor=other)
		self.assertIsNone(shared_sponsor_id(patent, trial))
		self.assertIsNone(describe_link(patent, trial)["sponsor_id"])

	def test_no_sponsor_on_either_side(self):
		patent = self.make_patent(individual=True)
		trial = self.make_trial()
		Trials.objects.filter(pk=trial.pk).update(primary_sponsor_normalized=None)
		trial.refresh_from_db()
		self.assertIsNone(describe_link(patent, trial)["sponsor_id"])

	def test_basis_is_category_subject_or_none(self):
		patent, trial = self.make_patent(), self.make_trial()
		self.assertEqual(describe_link(patent, trial)["basis"], "subject")
		PatentCategoryAssignment.objects.create(patents=patent, teamcategory=self.category)
		TrialCategoryAssignment.objects.create(trials=trial, teamcategory=self.category)
		self.assertEqual(describe_link(patent, trial)["basis"], "category")
		unrelated = self.make_trial(subject=self.other_subject)
		self.assertIsNone(describe_link(patent, unrelated)["basis"])


class LabelTests(LinkTestCase):
	def test_publication_and_title(self):
		patent = self.make_patent()
		patent.representative_publication = "WO2024123456A1"
		patent.title = "Remyelination compounds"
		self.assertEqual(patent_label(patent), "WO2024123456A1 · Remyelination compounds")

	def test_leaves_out_empty_parts_and_truncates_the_title(self):
		patent = self.make_patent()
		patent.representative_publication = ""
		patent.title = "x" * 120
		label = patent_label(patent)
		self.assertEqual(len(label), 90)
		self.assertTrue(label.endswith("…"))
		patent.title = ""
		patent.representative_publication = "EP1"
		self.assertEqual(patent_label(patent), "EP1")


class LinkPatentTrialTests(LinkTestCase):
	def setUp(self):
		super().setUp()
		self.user = User.objects.create_user(username="ed", email="ed@example.com", password="x")
		self.patent = self.make_patent(D(2019, 6, 1))
		self.trial = self.make_trial()

	def history_types(self, link):
		return list(
			PatentTrialLink.history.filter(id=link.pk).order_by("history_id").values_list("history_type", flat=True)
		)

	def test_creates_a_manual_row_with_the_computed_fields(self):
		link, created = link_patent_trial(self.patent, self.trial, self.user)
		self.assertTrue(created)
		self.assertEqual(link.source, "manual")
		self.assertEqual(link.created_by, self.user)
		self.assertFalse(link.suppressed)
		self.assertEqual(link.sponsor, self.sponsor)
		self.assertEqual(link.timing, "before")
		self.assertEqual(link.days_before_start, (D(2020, 1, 1) - D(2019, 6, 1)).days)
		self.assertEqual(link.basis, "subject")
		self.assertEqual(self.history_types(link), ["+"])

	def test_a_pair_with_nothing_in_common_is_allowed(self):
		other = Sponsor.objects.create(name="Other", slug="other")
		patent = self.make_patent(sponsor=other)
		trial = self.make_trial(subject=self.other_subject, status="Completed", end=None)
		link, created = link_patent_trial(patent, trial, self.user)
		self.assertTrue(created)
		self.assertIsNone(link.sponsor)
		self.assertIsNone(link.basis)
		self.assertIsNone(link.timing)

	def test_a_repeat_returns_the_same_row(self):
		first, _ = link_patent_trial(self.patent, self.trial, self.user)
		again, created = link_patent_trial(self.patent, self.trial, self.user)
		self.assertFalse(created)
		self.assertEqual(again.pk, first.pk)
		self.assertEqual(PatentTrialLink.objects.count(), 1)
		self.assertEqual(self.history_types(first), ["+"])

	def test_a_visible_auto_row_is_returned_unchanged(self):
		auto = PatentTrialLink.objects.create(
			patent=self.patent, trial=self.trial, sponsor=self.sponsor, timing="during", basis="category"
		)
		link, created = link_patent_trial(self.patent, self.trial, self.user)
		self.assertFalse(created)
		self.assertEqual(link.pk, auto.pk)
		auto.refresh_from_db()
		self.assertEqual(auto.source, "auto")
		self.assertIsNone(auto.created_by)
		self.assertEqual((auto.timing, auto.basis), ("during", "category"))

	def test_a_suppressed_auto_row_comes_back_visible_and_manual(self):
		auto = PatentTrialLink.objects.create(
			patent=self.patent,
			trial=self.trial,
			sponsor=self.sponsor,
			timing="after",
			days_after_completion=12,
			basis="category",
			suppressed=True,
		)
		link, created = link_patent_trial(self.patent, self.trial, self.user)
		self.assertFalse(created)
		self.assertEqual(link.pk, auto.pk)
		auto.refresh_from_db()
		self.assertFalse(auto.suppressed)
		self.assertEqual(auto.source, "manual")
		self.assertEqual(auto.created_by, self.user)
		# It keeps what detection recorded.
		self.assertEqual((auto.timing, auto.days_after_completion, auto.basis), ("after", 12, "category"))
		self.assertEqual(self.history_types(auto), ["+", "~"])
		latest = PatentTrialLink.history.filter(id=auto.pk).order_by("-history_id").first()
		self.assertEqual((latest.source, latest.suppressed), ("manual", False))

	def test_visible_link(self):
		self.assertIsNone(visible_link(self.patent, self.trial))
		link, _ = link_patent_trial(self.patent, self.trial, self.user)
		self.assertEqual(visible_link(self.patent, self.trial), link)
		PatentTrialLink.objects.filter(pk=link.pk).update(suppressed=True)
		self.assertIsNone(visible_link(self.patent, self.trial))


class UnlinkPatentTrialTests(LinkTestCase):
	def setUp(self):
		super().setUp()
		self.user = User.objects.create_user(username="ed", email="ed@example.com", password="x")
		self.patent = self.make_patent()
		self.trial = self.make_trial()

	def test_a_manual_link_is_deleted_and_the_history_records_it(self):
		link, _ = link_patent_trial(self.patent, self.trial, self.user)
		self.assertEqual(unlink_patent_trial(self.patent, self.trial), "manual")
		self.assertFalse(PatentTrialLink.objects.filter(pk=link.pk).exists())
		self.assertEqual(PatentTrialLink.history.filter(id=link.pk, history_type="-").count(), 1)

	def test_an_auto_link_is_hidden(self):
		auto = PatentTrialLink.objects.create(
			patent=self.patent, trial=self.trial, sponsor=self.sponsor, timing="during", basis="category"
		)
		self.assertEqual(unlink_patent_trial(self.patent, self.trial), "auto")
		auto.refresh_from_db()
		self.assertTrue(auto.suppressed)
		self.assertEqual(auto.source, "auto")
		self.assertEqual(PatentTrialLink.history.filter(id=auto.pk, history_type="~").count(), 1)

	def test_an_unlinked_pair_gives_none(self):
		self.assertIsNone(unlink_patent_trial(self.patent, self.trial))

	def test_an_already_hidden_link_gives_none(self):
		PatentTrialLink.objects.create(
			patent=self.patent,
			trial=self.trial,
			sponsor=self.sponsor,
			timing="during",
			basis="category",
			suppressed=True,
		)
		self.assertIsNone(unlink_patent_trial(self.patent, self.trial))

	def test_unlinking_then_linking_again_makes_a_new_manual_link(self):
		link_patent_trial(self.patent, self.trial, self.user)
		unlink_patent_trial(self.patent, self.trial)
		link, created = link_patent_trial(self.patent, self.trial, self.user)
		self.assertTrue(created)
		self.assertEqual(link.source, "manual")


class DetectionLeavesManualLinksAloneTests(LinkTestCase):
	def setUp(self):
		super().setUp()
		self.user = User.objects.create_user(username="ed", email="ed@example.com", password="x")

	def manual(self, patent, trial):
		link, _ = link_patent_trial(patent, trial, self.user)
		return link

	def snapshot(self, link):
		link.refresh_from_db()
		return (
			link.source,
			link.created_by_id,
			link.sponsor_id,
			link.timing,
			link.days_before_start,
			link.days_after_completion,
			link.basis,
			link.suppressed,
		)

	def test_survives_a_run_where_the_pair_does_not_match(self):
		# Filed years before the trial: detection never links this, and must not remove it.
		patent, trial = self.make_patent(D(2015, 1, 1)), self.make_trial()
		link = self.manual(patent, trial)
		before = self.snapshot(link)
		out = self.detect()
		self.assertEqual(self.snapshot(link), before)
		self.assertEqual(PatentTrialLink.objects.count(), 1)
		self.assertIn("remove 0", out)

	def test_survives_reset(self):
		patent = self.make_patent(D(2021, 3, 1))
		manual_trial, detected_trial = self.make_trial(), self.make_trial()
		link = self.manual(patent, manual_trial)
		PatentTrialLink.objects.create(
			patent=patent, trial=detected_trial, sponsor=self.sponsor, timing="during", basis="subject"
		)
		before = self.snapshot(link)
		out = self.detect(reset=True)
		self.assertIn("Deleted 1 existing links", out)
		self.assertEqual(self.snapshot(link), before)
		# The detected one is rebuilt by the scan that follows the reset.
		self.assertEqual(self.link(patent, detected_trial).source, "auto")

	def test_survives_reset_on_a_detected_pair_too(self):
		patent, trial = self.make_patent(), self.make_trial()
		link = self.manual(patent, trial)
		before = self.snapshot(link)
		self.detect(reset=True)
		self.assertEqual(PatentTrialLink.objects.count(), 1)
		self.assertEqual(self.snapshot(link), before)

	def test_a_detected_match_does_not_change_it(self):
		patent, trial = self.make_patent(D(2021, 3, 1)), self.make_trial()
		link = self.manual(patent, trial)
		# Make detection want something different: a category now shared.
		PatentCategoryAssignment.objects.create(patents=patent, teamcategory=self.category)
		TrialCategoryAssignment.objects.create(trials=trial, teamcategory=self.category)
		before = self.snapshot(link)
		self.assertEqual(before[6], "subject")
		out = self.detect()
		self.assertEqual(self.snapshot(link), before)
		self.assertEqual(PatentTrialLink.objects.count(), 1)
		self.assertIn("create 0, update 0", out)
		self.detect(reset=True)
		self.assertEqual(self.snapshot(link), before)

	def test_the_summary_counts_manual_links_left_alone(self):
		patent, trial = self.make_patent(D(2021, 3, 1)), self.make_trial()
		self.manual(patent, trial)
		out = self.detect()
		self.assertIn("1 manual link(s) left alone", out)
		self.assertIn("0 suppressed link(s) left alone", out)

	def test_a_manual_row_is_not_counted_as_created_in_a_dry_run(self):
		patent, trial = self.make_patent(D(2021, 3, 1)), self.make_trial()
		self.manual(patent, trial)
		out = self.detect(dry_run=True)
		self.assertIn("Would create 0", out)
		self.assertIn("1 manual link(s) left alone", out)

	def test_a_manual_row_wins_over_the_suppressed_branch(self):
		patent, trial = self.make_patent(D(2021, 3, 1)), self.make_trial()
		link = self.manual(patent, trial)
		PatentTrialLink.objects.filter(pk=link.pk).update(suppressed=True)
		out = self.detect()
		self.assertIn("1 manual link(s) left alone", out)
		self.assertIn("0 suppressed link(s) left alone", out)

	def test_a_hidden_auto_row_made_manual_survives_detection_no_longer_matching(self):
		patent, trial = self.make_patent(), self.make_trial()
		self.detect()
		PatentTrialLink.objects.filter(patent=patent, trial=trial).update(suppressed=True)
		link, _ = link_patent_trial(patent, trial, self.user)
		Trials.objects.filter(pk=trial.pk).update(primary_completion_date=D(2020, 2, 1))
		self.detect(after_months=1)
		self.assertTrue(PatentTrialLink.objects.filter(pk=link.pk, source="manual").exists())

	def test_other_patents_are_still_detected(self):
		patent, trial = self.make_patent(D(2021, 3, 1)), self.make_trial()
		self.manual(patent, trial)
		other = self.make_patent(D(2021, 3, 1))
		self.detect()
		self.assertIsNotNone(self.link(other, trial))
		self.assertEqual(PatentTrialLink.objects.filter(source="auto").count(), 1)
