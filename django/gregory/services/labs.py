"""Labs: reading the inputs for the grouping, writing its result, and merging.

The rules themselves are in ``gregory.services.lab_grouping`` (pure, no database).
Here: ``load_inputs`` reads everything the rules need in a handful of ``values_list``
queries, ``apply_labs`` diffs the result against the database and writes only what
changed, and ``merge_labs`` and ``merge_author_lab_rows`` carry labs through a merge.

Every write goes through simple-history's bulk helpers, so ``Lab`` and ``LabMembership``
history stays complete, and a rebuild with nothing new to say writes nothing.
"""

from collections import defaultdict
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone
from simple_history.utils import bulk_create_with_history, bulk_update_with_history

from gregory.models import ArticleAuthorship, Articles, Authors, Lab, LabMembership
from gregory.services.lab_grouping import (  # noqa: F401 (compute_labs re-exported)
	ROLE_HEAD,
	ROLE_MEMBER,
	compute_labs,
)

BATCH_SIZE = 1000
REASON_HEAD_DELETED = "head was deleted"
REASON_HEAD_UNQUALIFIED = "head no longer qualifies"
MANAGED_REASONS = (REASON_HEAD_DELETED, REASON_HEAD_UNQUALIFIED)
SAFETY_STOP_SHARE = 0.5  # a run deleting more than this share of existing labs aborts


class LabRebuildAborted(Exception):
	"""The run would delete more than half of the existing labs."""


@dataclass
class LabInputs:
	rows: list
	authors: dict
	article_years: dict
	article_author_counts: dict
	manual_heads: set
	existing_heads: set
	not_heads: set

	def as_kwargs(self):
		return dict(
			authors=self.authors,
			article_years=self.article_years,
			article_author_counts=self.article_author_counts,
			manual_heads=self.manual_heads,
			existing_heads=self.existing_heads,
			not_heads=self.not_heads,
		)


def load_inputs():
	"""Everything ``compute_labs`` needs, in seven ``values_list`` queries."""
	institutions = defaultdict(list)
	through = ArticleAuthorship.institutions.through
	for authorship_id, institution_id in through.objects.values_list(
		"articleauthorship_id", "institution_id"
	).iterator(chunk_size=20000):
		institutions[authorship_id].append(institution_id)
	rows = [
		(article_id, author_id, position, tuple(institutions.get(pk, ())))
		for pk, article_id, author_id, position in ArticleAuthorship.objects.values_list(
			"pk", "article_id", "author_id", "position"
		).iterator(chunk_size=20000)
	]

	article_author_counts = defaultdict(int)
	articles_per_author = defaultdict(int)
	for article_id, author_id in Articles.authors.through.objects.values_list(
		"articles_id", "authors_id"
	).iterator(chunk_size=20000):
		article_author_counts[article_id] += 1
		articles_per_author[author_id] += 1

	authors = {
		author_id: (openalex_id, orcid, articles_per_author.get(author_id, 0))
		for author_id, openalex_id, orcid in Authors.objects.values_list(
			"author_id", "openalex_id", "ORCID"
		).iterator(chunk_size=20000)
	}
	article_years = {
		article_id: published.year
		for article_id, published in Articles.objects.filter(
			published_date__isnull=False
		).values_list("article_id", "published_date").iterator(chunk_size=20000)
	}
	labs = list(Lab.objects.filter(head__isnull=False).values_list("head_id", "is_manual"))
	# Placed by hand as something other than a head: never the head of a computed lab.
	not_heads = set(
		LabMembership.objects.filter(source=LabMembership.Source.MANUAL)
		.exclude(role=LabMembership.Role.HEAD)
		.values_list("author_id", flat=True)
	)
	return LabInputs(
		rows=rows,
		authors=authors,
		article_years=article_years,
		article_author_counts=dict(article_author_counts),
		manual_heads={head for head, manual in labs if manual},
		existing_heads={head for head, _ in labs},
		not_heads=not_heads,
	)


@dataclass
class RebuildStats:
	labs_created: int = 0
	labs_updated: int = 0
	labs_deleted: int = 0
	labs_flagged: int = 0  # labs now needing review (newly flagged or still flagged)
	memberships_created: int = 0
	memberships_updated: int = 0
	memberships_deleted: int = 0
	manual_memberships_left: int = 0
	manual_labs_left: int = 0

	@property
	def changed(self):
		return any(
			getattr(self, f)
			for f in (
				"labs_created",
				"labs_updated",
				"labs_deleted",
				"memberships_created",
				"memberships_updated",
				"memberships_deleted",
			)
		)


def apply_labs(result, *, dry_run=False, force=False, now=None):
	"""Write a ``LabsResult`` to the database, following the rebuild's rules.

	Never touches a manual membership; never renames, re-heads, re-homes or deletes a
	manual lab (its computed members and cached counts are kept current). Raises
	``LabRebuildAborted`` when more than half of the existing labs would be deleted,
	unless ``force``. With ``dry_run`` nothing is written. Returns ``RebuildStats``.
	"""
	now = now or timezone.now()
	stats = RebuildStats()
	labs = list(Lab.objects.all())
	memberships = {m.author_id: m for m in LabMembership.objects.all()}
	manual_lab_ids = {m.lab_id for m in memberships.values() if m.source == "manual" and m.lab_id}
	by_head = {lab.head_id: lab for lab in labs if lab.head_id}

	lab_for_head = {}
	labs_to_create, labs_to_update, labs_to_delete = [], [], []
	flagged = set()

	for head_id, info in result.labs.items():
		lab = by_head.get(head_id)
		if lab is None:
			lab = Lab(
				head_id=head_id,
				institution_id=info.institution_id,
				member_count=info.member_count,
				core_count=info.core_count,
				collaborator_count=info.collaborator_count,
				last_author_articles=info.last_author_articles,
				computed_at=now,
			)
			labs_to_create.append(lab)
			lab_for_head[head_id] = lab
			continue
		lab_for_head[head_id] = lab
		changed = False
		for name in ("member_count", "core_count", "collaborator_count", "last_author_articles"):
			if getattr(lab, name) != getattr(info, name):
				setattr(lab, name, getattr(info, name))
				changed = True
		if (not lab.is_manual or lab.institution_id is None) and lab.institution_id != info.institution_id:
			lab.institution_id = info.institution_id
			changed = True
		if changed:
			lab.computed_at = now
		if lab.needs_review and lab.review_reason in MANAGED_REASONS:
			lab.needs_review = False
			lab.review_reason = ""
			changed = True
		if changed:
			labs_to_update.append(lab)
		if lab.is_manual:
			stats.manual_labs_left += 1

	for lab in labs:
		if lab.head_id and lab.head_id in result.labs:
			continue
		if lab.is_manual:
			stats.manual_labs_left += 1
			if not lab.head_id:
				_flag(lab, REASON_HEAD_DELETED, labs_to_update, flagged)
			continue
		if not lab.head_id:
			_flag(lab, REASON_HEAD_DELETED, labs_to_update, flagged)
		elif lab.pk in manual_lab_ids:
			_flag(lab, REASON_HEAD_UNQUALIFIED, labs_to_update, flagged)
		else:
			labs_to_delete.append(lab)

	if labs and len(labs_to_delete) > len(labs) * SAFETY_STOP_SHARE and not force:
		raise LabRebuildAborted(
			f"This run would delete {len(labs_to_delete)} of {len(labs)} existing labs. "
			"That usually means the authorship table is empty or half loaded. "
			"Re-run with --force if it is intended."
		)

	stats.labs_created = len(labs_to_create)
	stats.labs_updated = len(labs_to_update)
	stats.labs_deleted = len(labs_to_delete)
	stats.labs_flagged = len(flagged)

	with transaction.atomic():
		if not dry_run:
			if labs_to_create:
				bulk_create_with_history(labs_to_create, Lab, batch_size=BATCH_SIZE)
			if labs_to_update:
				bulk_update_with_history(
					labs_to_update,
					Lab,
					[
						"member_count",
						"core_count",
						"collaborator_count",
						"last_author_articles",
						"institution",
						"needs_review",
						"review_reason",
						"computed_at",
					],
					batch_size=BATCH_SIZE,
				)

		members_to_create, members_to_update, members_to_delete = [], [], []
		for author_id, asg in result.assignments.items():
			lab = lab_for_head[asg.head_id]
			existing = memberships.get(author_id)
			if existing is None:
				members_to_create.append(
					LabMembership(
						author_id=author_id,
						lab=lab,
						role=asg.role,
						same_institution=asg.same_institution,
						source=LabMembership.Source.COMPUTED,
						shared_articles=asg.shared_articles,
						last_shared_year=asg.last_shared_year,
					)
				)
			elif existing.source == LabMembership.Source.MANUAL:
				continue
			elif (
				existing.lab_id != lab.pk
				or existing.role != asg.role
				or existing.same_institution != asg.same_institution
				or existing.shared_articles != asg.shared_articles
				or existing.last_shared_year != asg.last_shared_year
			):
				existing.lab = lab
				existing.role = asg.role
				existing.same_institution = asg.same_institution
				existing.shared_articles = asg.shared_articles
				existing.last_shared_year = asg.last_shared_year
				members_to_update.append(existing)
		for author_id, membership in memberships.items():
			if membership.source == LabMembership.Source.MANUAL:
				stats.manual_memberships_left += 1
			elif author_id not in result.assignments:
				members_to_delete.append(membership.pk)

		stats.memberships_created = len(members_to_create)
		stats.memberships_updated = len(members_to_update)
		stats.memberships_deleted = len(members_to_delete)

		if not dry_run:
			if members_to_create:
				bulk_create_with_history(members_to_create, LabMembership, batch_size=BATCH_SIZE)
			if members_to_update:
				bulk_update_with_history(
					members_to_update,
					LabMembership,
					["lab", "role", "same_institution", "shared_articles", "last_shared_year"],
					batch_size=BATCH_SIZE,
				)
			# Deleted through the ORM, one by one inside the collector, so each deletion
			# reaches simple-history.
			for start in range(0, len(members_to_delete), BATCH_SIZE):
				LabMembership.objects.filter(
					pk__in=members_to_delete[start : start + BATCH_SIZE]
				).delete()
			for start in range(0, len(labs_to_delete), BATCH_SIZE):
				Lab.objects.filter(
					pk__in=[lab.pk for lab in labs_to_delete[start : start + BATCH_SIZE]]
				).delete()
	return stats


def _flag(lab, reason, labs_to_update, flagged):
	flagged.add(lab.pk)
	if not lab.needs_review or lab.review_reason != reason:
		lab.needs_review = True
		lab.review_reason = reason
		labs_to_update.append(lab)


def merge_labs(keep, remove):
	"""Merge every lab in ``remove`` into ``keep`` and delete them.

	The removed labs' memberships move to ``keep`` (an author has one membership row, so
	none can collide), a removed head becoming a member. They are marked manual: a
	rebuild would otherwise put them straight back. ``keep`` becomes manual. Returns the
	number of memberships moved. Assumes it runs inside ``transaction.atomic()``.
	"""
	remove = [lab for lab in remove if lab.pk != keep.pk]
	moved = 0
	for lab in remove:
		for membership in list(lab.memberships.all()):
			membership.lab = keep
			membership.source = LabMembership.Source.MANUAL
			if membership.role == LabMembership.Role.HEAD:
				membership.role = LabMembership.Role.MEMBER
			membership.save()
			moved += 1
		lab.delete()
	if not keep.is_manual:
		keep.is_manual = True
		keep.save()
	return moved


def merge_author_lab_rows(keep, remove):
	"""Carry ``remove``'s lab rows to ``keep`` before ``remove`` is deleted.

	* Head: when ``remove`` heads a lab and ``keep`` doesn't, the lab's head becomes
	  ``keep``, who becomes its head member (replacing a membership they held). When both
	  head labs, the removed author's lab is merged into the kept one's. A head's role
	  takes precedence, so the membership rule below doesn't apply to heads.
	* Membership: a manual one wins over a computed one; if both are manual the kept
	  author's stays. ``remove``'s own row is dropped with the author.
	"""
	removed_lab = Lab.objects.filter(head=remove).first()
	kept_lab = Lab.objects.filter(head=keep).first()
	if removed_lab and kept_lab:
		merge_labs(kept_lab, [removed_lab])
		return
	if removed_lab:
		old = LabMembership.objects.filter(author=remove).first()
		removed_lab.head = keep
		removed_lab.save()
		LabMembership.objects.filter(author=keep).delete()
		LabMembership.objects.create(
			author=keep,
			lab=removed_lab,
			role=LabMembership.Role.HEAD,
			source=(
				LabMembership.Source.MANUAL
				if removed_lab.is_manual
				else LabMembership.Source.COMPUTED
			),
			shared_articles=old.shared_articles if old else 0,
			last_shared_year=old.last_shared_year if old else None,
		)
		return
	if kept_lab:
		return  # the kept author's head membership stays
	removed_membership = LabMembership.objects.filter(author=remove).first()
	kept_membership = LabMembership.objects.filter(author=keep).first()
	if (
		removed_membership
		and removed_membership.source == LabMembership.Source.MANUAL
		and not (kept_membership and kept_membership.source == LabMembership.Source.MANUAL)
	):
		if kept_membership:
			kept_membership.delete()
		removed_membership.author = keep
		removed_membership.save()
