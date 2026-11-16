"""Tests for the database side of labs: apply_labs, the rebuild_labs command, load_inputs
and the lab rules of the author merge."""

from io import StringIO

from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import TestCase

from gregory.models import ArticleAuthorship, Articles, Authors, Institution, Lab, LabMembership
from gregory.services.author_merge import merge_authors
from gregory.services.lab_grouping import Assignment, LabResult, LabsResult
from gregory.services.labs import LabRebuildAborted, apply_labs, merge_labs

COMPUTED = LabMembership.Source.COMPUTED
MANUAL = LabMembership.Source.MANUAL


def author(n):
	return Authors.objects.create(given_name=f"Given{n}", family_name=f"Family{n}")


def result(*labs):
	"""``labs``: ``(head, [(author, role, shared), ...], extra LabResult kwargs)``."""
	out = LabsResult()
	for head, members, *extra in labs:
		kwargs = extra[0] if extra else {}
		info = LabResult(head.pk, [head.pk], kwargs.pop("institution_id", None), 3, **kwargs)
		out.labs[head.pk] = info
		out.assignments[head.pk] = Assignment(head.pk, head.pk, "head", None, 3, 2025)
		for member, role, shared in members:
			out.assignments[member.pk] = Assignment(member.pk, head.pk, role, True, shared, 2024)
	return out


def counts():
	return (
		Lab.history.count(),
		LabMembership.history.count(),
		Lab.objects.count(),
		LabMembership.objects.count(),
	)


class ApplyCreateTests(TestCase):
	def setUp(self):
		self.h, self.m, self.c = author(1), author(2), author(3)

	def test_creates_labs_and_memberships_with_history(self):
		stats = apply_labs(
			result((self.h, [(self.m, "member", 2), (self.c, "collaborator", 1)], {"member_count": 1}))
		)
		lab = Lab.objects.get()
		self.assertEqual((lab.head, lab.is_manual, lab.member_count), (self.h, False, 1))
		self.assertIsNotNone(lab.computed_at)
		self.assertEqual(LabMembership.objects.count(), 3)
		m = LabMembership.objects.get(author=self.m)
		self.assertEqual((m.lab, m.role, m.source, m.shared_articles), (lab, "member", COMPUTED, 2))
		self.assertEqual(Lab.history.filter(history_type="+").count(), 1)
		self.assertEqual(LabMembership.history.filter(history_type="+").count(), 3)
		self.assertEqual((stats.labs_created, stats.memberships_created), (1, 3))

	def test_a_second_run_with_the_same_result_writes_nothing(self):
		res = result((self.h, [(self.m, "member", 2)], {"member_count": 1}))
		apply_labs(res)
		before = counts()
		stats = apply_labs(res)
		self.assertEqual(counts(), before)
		self.assertFalse(stats.changed)

	def test_dry_run_writes_nothing_but_reports(self):
		stats = apply_labs(result((self.h, [(self.m, "member", 2)])), dry_run=True)
		self.assertEqual(counts(), (0, 0, 0, 0))
		self.assertEqual((stats.labs_created, stats.memberships_created), (1, 2))

	def test_a_changed_computed_membership_is_updated_and_recorded(self):
		apply_labs(result((self.h, [(self.m, "member", 2)])))
		apply_labs(result((self.h, [(self.m, "member", 5)])))
		m = LabMembership.objects.get(author=self.m)
		self.assertEqual(m.shared_articles, 5)
		self.assertEqual(LabMembership.history.filter(author=self.m, history_type="~").count(), 1)

	def test_a_computed_member_moves_to_another_lab(self):
		other = author(4)
		apply_labs(result((self.h, [(self.m, "member", 2)]), (other, [])))
		apply_labs(result((self.h, []), (other, [(self.m, "member", 2)])))
		self.assertEqual(LabMembership.objects.get(author=self.m).lab.head, other)

	def test_a_computed_membership_with_no_assignment_is_deleted(self):
		apply_labs(result((self.h, [(self.m, "member", 2)])))
		apply_labs(result((self.h, [])))
		self.assertFalse(LabMembership.objects.filter(author=self.m).exists())
		self.assertEqual(LabMembership.history.filter(author=self.m, history_type="-").count(), 1)

	def test_the_labs_institution_is_set_and_follows_the_computation(self):
		a, b = (Institution.objects.create(openalex_id=f"I{i}", name=f"Inst {i}") for i in (1, 2))
		apply_labs(result((self.h, [], {"institution_id": a.pk})))
		self.assertEqual(Lab.objects.get().institution, a)
		apply_labs(result((self.h, [], {"institution_id": b.pk})))
		self.assertEqual(Lab.objects.get().institution, b)


class ApplyManualTests(TestCase):
	def setUp(self):
		self.h, self.m, self.c = author(1), author(2), author(3)

	def test_a_manual_membership_is_untouched(self):
		other = Lab.objects.create(head=author(9), is_manual=True)
		LabMembership.objects.create(author=self.m, lab=other, role="member", source=MANUAL)
		stats = apply_labs(result((self.h, [(self.m, "member", 2)])))
		m = LabMembership.objects.get(author=self.m)
		self.assertEqual((m.lab, m.source), (other, MANUAL))
		self.assertEqual(LabMembership.history.filter(author=self.m).count(), 1)
		self.assertGreaterEqual(stats.manual_memberships_left, 1)

	def test_a_pinned_no_lab_membership_is_untouched(self):
		LabMembership.objects.create(author=self.m, lab=None, role="member", source=MANUAL)
		apply_labs(result((self.h, [(self.m, "member", 2)])))
		m = LabMembership.objects.get(author=self.m)
		self.assertEqual((m.lab, m.source), (None, MANUAL))

	def test_a_manual_lab_is_kept_as_is_while_its_computed_members_update(self):
		inst = Institution.objects.create(openalex_id="I1", name="Mine")
		lab = Lab.objects.create(
			head=self.h, name="Our lab", is_manual=True, institution=inst, notes="n"
		)
		res = result((self.h, [(self.m, "member", 2)], {"member_count": 1, "institution_id": None}))
		apply_labs(res)
		lab.refresh_from_db()
		self.assertEqual((lab.name, lab.head, lab.institution, lab.is_manual), ("Our lab", self.h, inst, True))
		self.assertEqual(lab.member_count, 1)
		self.assertEqual(LabMembership.objects.get(author=self.m).lab, lab)

	def test_a_manual_lab_with_no_institution_gets_the_computed_one(self):
		inst = Institution.objects.create(openalex_id="I1", name="Computed")
		Lab.objects.create(head=self.h, is_manual=True)
		apply_labs(result((self.h, [], {"institution_id": inst.pk})))
		self.assertEqual(Lab.objects.get().institution, inst)

	def test_a_manual_lab_the_result_no_longer_has_is_never_deleted(self):
		Lab.objects.create(head=self.h, is_manual=True)
		apply_labs(result((author(5), [])))
		self.assertTrue(Lab.objects.filter(head=self.h).exists())


class ApplyStaleTests(TestCase):
	def setUp(self):
		self.h, self.g, self.m = author(1), author(2), author(3)
		apply_labs(result((self.h, [(self.m, "member", 2)]), (self.g, [])))

	def test_a_stale_computed_lab_is_deleted_with_its_memberships(self):
		stats = apply_labs(result((self.g, [])))
		self.assertFalse(Lab.objects.filter(head=self.h).exists())
		self.assertFalse(LabMembership.objects.filter(author__in=[self.h, self.m]).exists())
		self.assertEqual(stats.labs_deleted, 1)
		self.assertEqual(Lab.history.filter(history_type="-").count(), 1)

	def test_a_stale_lab_with_a_manual_member_stays_flagged(self):
		lab = Lab.objects.get(head=self.h)
		LabMembership.objects.filter(author=self.m).update(source=MANUAL)
		stats = apply_labs(result((self.g, [])))
		lab.refresh_from_db()
		self.assertEqual((lab.needs_review, lab.review_reason), (True, "head no longer qualifies"))
		self.assertEqual(LabMembership.objects.get(author=self.m).lab, lab)
		self.assertEqual((stats.labs_deleted, stats.labs_flagged), (0, 1))

	def test_a_flagged_lab_is_left_alone_on_the_next_run(self):
		LabMembership.objects.filter(author=self.m).update(source=MANUAL)
		apply_labs(result((self.g, [])))
		before = counts()
		stats = apply_labs(result((self.g, [])))
		self.assertEqual(counts(), before)
		self.assertEqual(stats.labs_flagged, 1)

	def test_the_flag_clears_when_the_head_qualifies_again(self):
		lab = Lab.objects.get(head=self.h)
		LabMembership.objects.filter(author=self.m).update(source=MANUAL)
		apply_labs(result((self.g, [])))
		apply_labs(result((self.h, []), (self.g, [])))
		lab.refresh_from_db()
		self.assertEqual((lab.needs_review, lab.review_reason), (False, ""))

	def test_a_lab_whose_head_was_deleted_is_flagged_not_re_headed(self):
		lab = Lab.objects.get(head=self.h)
		self.h.delete()
		lab.refresh_from_db()
		self.assertIsNone(lab.head)
		apply_labs(result((self.g, [])))
		lab.refresh_from_db()
		self.assertEqual((lab.needs_review, lab.review_reason, lab.head), (True, "head was deleted", None))

	def test_the_flag_of_a_hand_set_review_without_a_managed_reason_stays(self):
		lab = Lab.objects.get(head=self.g)
		lab.needs_review, lab.review_reason = True, "check the name"
		lab.save()
		apply_labs(result((self.h, [(self.m, "member", 2)]), (self.g, [])))
		lab.refresh_from_db()
		self.assertEqual((lab.needs_review, lab.review_reason), (True, "check the name"))


class SafetyStopTests(TestCase):
	def setUp(self):
		self.heads = [author(i) for i in range(4)]
		apply_labs(result(*[(h, []) for h in self.heads]))

	def test_deleting_more_than_half_aborts(self):
		before = counts()
		with self.assertRaises(LabRebuildAborted):
			apply_labs(result((self.heads[0], [])))
		self.assertEqual(counts(), before)

	def test_exactly_half_is_allowed(self):
		apply_labs(result(*[(h, []) for h in self.heads[:2]]))
		self.assertEqual(Lab.objects.count(), 2)

	def test_force_overrides_it(self):
		apply_labs(result((self.heads[0], [])), force=True)
		self.assertEqual(Lab.objects.count(), 1)

	def test_a_dry_run_reports_the_stop_too(self):
		with self.assertRaises(LabRebuildAborted):
			apply_labs(result((self.heads[0], [])), dry_run=True)

	def test_an_empty_result_against_no_labs_is_fine(self):
		Lab.objects.all().delete()
		apply_labs(LabsResult())


class CommandTests(TestCase):
	"""End to end through load_inputs, compute_labs and apply_labs."""

	def setUp(self):
		self.head, self.member = author(1), author(2)
		for i in range(3):
			article = Articles.objects.create(
				title=f"Paper {i}", link=f"https://ex.org/{i}", published_date="2025-06-01T00:00:00Z"
			)
			article.authors.add(self.head, self.member)
			ArticleAuthorship.objects.create(
				article=article, author=self.member, position="first", author_index=0
			)
			ArticleAuthorship.objects.create(
				article=article, author=self.head, position="last", author_index=1
			)

	def run_command(self, *args):
		out = StringIO()
		call_command("rebuild_labs", *args, stdout=out)
		return out.getvalue()

	def test_builds_the_lab_and_prints_the_counts(self):
		out = self.run_command()
		lab = Lab.objects.get()
		self.assertEqual((lab.head, lab.member_count, lab.last_author_articles), (self.head, 1, 3))
		self.assertEqual(LabMembership.objects.get(author=self.member).role, "member")
		self.assertIn("1 created", out)

	def test_a_second_run_writes_nothing(self):
		self.run_command()
		before = counts()
		out = self.run_command()
		self.assertEqual(counts(), before)
		self.assertIn("0 created, 0 updated, 0 deleted", out)

	def test_dry_run_writes_nothing(self):
		out = self.run_command("--dry-run")
		self.assertEqual(counts(), (0, 0, 0, 0))
		self.assertIn("Would change", out)

	def test_the_safety_stop_is_a_command_error_and_force_overrides_it(self):
		self.run_command()
		ArticleAuthorship.objects.all().delete()
		with self.assertRaises(CommandError):
			self.run_command()
		self.assertEqual(Lab.objects.count(), 1)
		self.run_command("--force")
		self.assertEqual(Lab.objects.count(), 0)

	def test_two_rows_of_one_person_make_one_lab(self):
		twin = Authors.objects.create(given_name="Given1", family_name="Family1x")
		Authors.objects.filter(pk__in=[self.head.pk, twin.pk]).update(openalex_id="A1")
		self.run_command()
		self.assertEqual(Lab.objects.get().head, self.head)

	def test_a_head_moved_by_hand_to_another_lab_does_not_get_their_lab_back(self):
		self.run_command()
		other = Lab.objects.create(head=author(9), is_manual=True)
		LabMembership.objects.filter(author=self.head).update(
			lab=other, role="member", source=MANUAL
		)
		self.run_command("--force")  # the only computed lab goes, which trips the safety stop
		self.assertFalse(Lab.objects.filter(head=self.head).exists())
		moved = LabMembership.objects.get(author=self.head)
		self.assertEqual((moved.lab, moved.role, moved.source), (other, "member", MANUAL))
		self.assertFalse(LabMembership.objects.filter(author=self.member).exists())

	def test_load_inputs_runs_a_fixed_number_of_queries(self):
		from gregory.services.labs import load_inputs

		with self.assertNumQueries(7):
			inputs = load_inputs()
		self.assertEqual(len(inputs.rows), 6)
		self.assertEqual(inputs.authors[self.head.pk][2], 3)


class MergeAuthorLabTests(TestCase):
	def setUp(self):
		self.keep, self.remove = author(1), author(2)

	def membership(self, who, lab, source, role="member"):
		return LabMembership.objects.create(author=who, lab=lab, role=role, source=source)

	def test_a_manual_membership_wins_over_a_computed_one(self):
		lab_a, lab_b = Lab.objects.create(head=author(8)), Lab.objects.create(head=author(9))
		self.membership(self.keep, lab_a, COMPUTED)
		self.membership(self.remove, lab_b, MANUAL)
		merge_authors(self.keep, [self.remove])
		m = LabMembership.objects.get(author=self.keep)
		self.assertEqual((m.lab, m.source), (lab_b, MANUAL))

	def test_a_computed_membership_does_not_win_over_none_or_a_computed_one(self):
		lab_a, lab_b = Lab.objects.create(head=author(8)), Lab.objects.create(head=author(9))
		self.membership(self.keep, lab_a, COMPUTED)
		self.membership(self.remove, lab_b, COMPUTED)
		merge_authors(self.keep, [self.remove])
		self.assertEqual(LabMembership.objects.get(author=self.keep).lab, lab_a)
		self.assertEqual(LabMembership.objects.count(), 1)

	def test_when_both_are_manual_the_kept_authors_stays(self):
		lab_a, lab_b = Lab.objects.create(head=author(8)), Lab.objects.create(head=author(9))
		self.membership(self.keep, lab_a, MANUAL)
		self.membership(self.remove, lab_b, MANUAL)
		merge_authors(self.keep, [self.remove])
		self.assertEqual(LabMembership.objects.get(author=self.keep).lab, lab_a)

	def test_a_manual_pin_to_no_lab_on_the_removed_author_carries_over(self):
		self.membership(self.remove, None, MANUAL)
		merge_authors(self.keep, [self.remove])
		m = LabMembership.objects.get(author=self.keep)
		self.assertEqual((m.lab, m.source), (None, MANUAL))

	def test_a_lab_the_removed_author_heads_passes_to_the_kept_one(self):
		lab = Lab.objects.create(head=self.remove, name="Named", is_manual=True)
		self.membership(self.remove, lab, MANUAL, role="head")
		other = Lab.objects.create(head=author(9))
		self.membership(self.keep, other, COMPUTED)
		merge_authors(self.keep, [self.remove])
		lab.refresh_from_db()
		self.assertEqual(lab.head, self.keep)
		m = LabMembership.objects.get(author=self.keep)
		self.assertEqual((m.lab, m.role, m.source), (lab, "head", MANUAL))

	def test_two_headed_labs_merge_into_the_kept_authors(self):
		kept_lab = Lab.objects.create(head=self.keep)
		removed_lab = Lab.objects.create(head=self.remove)
		self.membership(self.keep, kept_lab, COMPUTED, role="head")
		self.membership(self.remove, removed_lab, COMPUTED, role="head")
		member = author(7)
		self.membership(member, removed_lab, COMPUTED)
		merge_authors(self.keep, [self.remove])
		self.assertEqual(list(Lab.objects.all()), [kept_lab])
		kept_lab.refresh_from_db()
		self.assertTrue(kept_lab.is_manual)
		self.assertEqual(LabMembership.objects.get(author=member).lab, kept_lab)
		self.assertEqual(LabMembership.objects.get(author=self.keep).role, "head")
		self.assertFalse(LabMembership.objects.filter(author_id=self.remove.pk).exists())

	def test_merging_with_no_lab_rows_still_works(self):
		merge_authors(self.keep, [self.remove])
		self.assertFalse(Authors.objects.filter(pk=self.remove.pk).exists())


class MergeLabsTests(TestCase):
	def test_memberships_move_the_other_labs_go_and_the_survivor_becomes_manual(self):
		keep, gone = Lab.objects.create(head=author(1)), Lab.objects.create(head=author(2))
		LabMembership.objects.create(author=keep.head, lab=keep, role="head")
		LabMembership.objects.create(author=gone.head, lab=gone, role="head")
		member = author(3)
		LabMembership.objects.create(author=member, lab=gone, role="member")
		moved = merge_labs(keep, [gone])
		self.assertEqual(moved, 2)
		self.assertFalse(Lab.objects.filter(pk=gone.pk).exists())
		keep.refresh_from_db()
		self.assertTrue(keep.is_manual)
		old_head = LabMembership.objects.get(author=gone.head)
		self.assertEqual((old_head.lab, old_head.role, old_head.source), (keep, "member", MANUAL))
		self.assertEqual(LabMembership.objects.get(author=member).source, MANUAL)
		self.assertEqual(LabMembership.objects.get(author=keep.head).role, "head")

	def test_an_author_can_never_have_two_memberships(self):
		a = author(1)
		lab = Lab.objects.create(head=author(2))
		LabMembership.objects.create(author=a, lab=lab)
		with self.assertRaises(IntegrityError), transaction.atomic():
			LabMembership.objects.create(author=a, lab=lab)

	def test_a_computed_membership_cannot_have_no_lab(self):
		with self.assertRaises(IntegrityError), transaction.atomic():
			LabMembership.objects.create(author=author(1), lab=None, source=COMPUTED)
