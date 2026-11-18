"""Link labs to the subjects and categories their papers cover.

``compute_topic_links`` counts, for every lab and topic, the papers by the lab's head and
members. ``apply_topic_links`` diffs that against the stored links and writes only what
changed, so a second run with the same data writes nothing.

A link exists at ``MIN_TOPIC_ARTICLES`` papers, or when a superuser added or hid it. The
rebuild writes counts only, never those two flags, and it only reads articles and their
relevance. Links carry history for what people change, so the rebuild's writes bypass it.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import NamedTuple

from django.db import transaction
from django.utils import timezone

from gregory.models import (
	ArticleCategoryAssignment,
	Articles,
	LabCategory,
	LabMembership,
	LAB_TOPIC_COMPUTED_FIELDS,
	LabSubject,
	Subject,
	TeamCategory,
)

MIN_TOPIC_ARTICLES = 3
RECENT_YEARS = 5  # from the start of the calendar year 4 years ago; later dates count too
BATCH_SIZE = 1000
SAFETY_STOP_SHARE = 0.5  # a run deleting more than this share of computed links aborts
LINK_MODELS = ((LabSubject, "subject_id"), (LabCategory, "category_id"))


class LabTopicRebuildAborted(Exception):
	"""The run would delete too many links; usually the inputs are empty or half loaded."""


class TopicCounts(NamedTuple):
	"""Named after the link's columns, which ``_plan_links`` copies them to."""

	article_count: int = 0
	relevant_count: int = 0
	recent_article_count: int = 0
	latest_article_year: int | None = None


@dataclass
class TopicLinksResult:
	"""Counts for every pair with a paper, below the threshold too: added and hidden links
	need current counts. Keyed by ``(lab_id, subject_id)`` and ``(lab_id, category_id)``."""

	subjects: dict = field(default_factory=dict)
	categories: dict = field(default_factory=dict)


@dataclass
class LinkStats:
	created: int = 0
	updated: int = 0
	deleted: int = 0
	manual_kept: int = 0  # added by a superuser, below the threshold
	hidden_kept: int = 0  # hidden by a superuser, below the threshold


@dataclass
class TopicRebuildStats:
	subjects: LinkStats = field(default_factory=LinkStats)
	categories: LinkStats = field(default_factory=LinkStats)


def load_article_labs():
	"""``{article_id: {lab_id, ...}}`` for the labs with a head or member on each paper.
	Collaborators and authors pinned to no lab add nothing."""
	author_labs = dict(
		LabMembership.objects.filter(
			lab__isnull=False,
			role__in=[LabMembership.Role.HEAD, LabMembership.Role.MEMBER],
		).values_list("author_id", "lab_id")
	)
	article_labs = defaultdict(set)
	authorships = Articles.authors.through.objects.values_list("articles_id", "authors_id")
	for article_id, author_id in authorships.iterator():
		lab_id = author_labs.get(author_id)
		if lab_id is not None:
			article_labs[article_id].add(lab_id)
	return dict(article_labs)


def count_topic(article_ids, relevant_ids, article_labs, article_years, first_recent_year):
	"""Count one topic's papers per lab: ``{lab_id: TopicCounts}``. A paper counts once per
	lab, however many of the lab's people wrote it."""
	totals = defaultdict(lambda: [0, 0, 0, None])
	for article_id in article_ids:
		year = article_years.get(article_id)
		for lab_id in article_labs.get(article_id, ()):
			counts = totals[lab_id]
			counts[0] += 1
			if article_id in relevant_ids:
				counts[1] += 1
			if year is not None:
				if year >= first_recent_year:
					counts[2] += 1
				if counts[3] is None or year > counts[3]:
					counts[3] = year
	return {lab_id: TopicCounts(*counts) for lab_id, counts in totals.items()}


def compute_topic_links(today=None):
	"""Count every lab's papers per subject and per category. Reads only.

	Relevant follows the API rule, per subject: an editor said so or the ML consensus does.
	A category's paper is relevant when it is relevant for one of the category's subjects.
	"""
	# api.filters imports gregory.models, so importing it at module level risks a cycle.
	from api.filters import relevant_articles_q

	today = today or timezone.now().date()
	first_recent_year = today.year - RECENT_YEARS + 1
	article_labs = load_article_labs()
	article_years = {
		article_id: published.year
		for article_id, published in Articles.objects.filter(published_date__isnull=False)
		.values_list("pk", "published_date")
		.iterator()
		if article_id in article_labs
	}

	def articles_by_topic(rows):
		grouped = defaultdict(set)
		for topic_id, article_id in rows.iterator():
			if article_id in article_labs:
				grouped[topic_id].add(article_id)
		return grouped

	subject_articles = articles_by_topic(
		Articles.subjects.through.objects.values_list("subject_id", "articles_id")
	)
	category_articles = articles_by_topic(
		ArticleCategoryAssignment.objects.values_list("teamcategory_id", "articles_id")
	)
	subject_relevant = {
		subject_id: set(
			Articles.objects.filter(subjects=subject_id)
			.filter(relevant_articles_q([subject_id], editor_verdict_wins=False))
			.values_list("pk", flat=True)
		)
		for subject_id in Subject.objects.values_list("pk", flat=True)
	}
	category_relevant = defaultdict(set)
	category_subjects = TeamCategory.subjects.through.objects.values_list(
		"teamcategory_id", "subject_id"
	)
	for category_id, subject_id in category_subjects:
		category_relevant[category_id] |= subject_relevant.get(subject_id, set())

	def count_topics(articles, relevant):
		links = {}
		for topic_id, article_ids in articles.items():
			counted = count_topic(
				article_ids,
				relevant.get(topic_id, set()),
				article_labs,
				article_years,
				first_recent_year,
			)
			for lab_id, counts in counted.items():
				links[(lab_id, topic_id)] = counts
		return links

	return TopicLinksResult(
		subjects=count_topics(subject_articles, subject_relevant),
		categories=count_topics(category_articles, category_relevant),
	)


@dataclass
class _LinkPlan:
	model: type
	stats: LinkStats
	computed_total: int = 0  # stored links with neither flag
	to_create: list = field(default_factory=list)
	to_update: list = field(default_factory=list)
	to_delete: list = field(default_factory=list)


def _stored_links(model):
	return list(model.objects.all())


def _plan_links(model, topic_attr, computed, stats, now):
	plan = _LinkPlan(model, stats)
	seen = set()
	for link in _stored_links(model):
		key = (link.lab_id, getattr(link, topic_attr))
		seen.add(key)
		counts = computed.get(key, TopicCounts())
		flagged = link.is_manual or link.is_hidden
		if not flagged:
			plan.computed_total += 1
		if counts.article_count < MIN_TOPIC_ARTICLES:
			if not flagged:
				plan.to_delete.append(link.pk)
				continue
			stats.manual_kept += link.is_manual
			stats.hidden_kept += link.is_hidden
		changed = False
		for name, value in counts._asdict().items():
			if getattr(link, name) != value:
				setattr(link, name, value)
				changed = True
		if changed:
			link.computed_at = now
			plan.to_update.append(link)
	for (lab_id, topic_id), counts in computed.items():
		if (lab_id, topic_id) not in seen and counts.article_count >= MIN_TOPIC_ARTICLES:
			plan.to_create.append(
				model(lab_id=lab_id, computed_at=now, **{topic_attr: topic_id}, **counts._asdict())
			)
	stats.created = len(plan.to_create)
	stats.updated = len(plan.to_update)
	stats.deleted = len(plan.to_delete)
	return plan


def _write_links(plan):
	model = plan.model
	# A link a superuser added since the stored links were read wins over the new row.
	model.objects.bulk_create(plan.to_create, batch_size=BATCH_SIZE, ignore_conflicts=True)
	# Counts only: a flag set during the run must survive.
	model.objects.bulk_update(plan.to_update, LAB_TOPIC_COMPUTED_FIELDS, batch_size=BATCH_SIZE)
	plan.stats.deleted = 0
	for start in range(0, len(plan.to_delete), BATCH_SIZE):
		# The flags are checked again for the same reason. A raw delete sends no signals, so
		# it adds nothing to the history, which is for what people change.
		plan.stats.deleted += model.objects.filter(
			pk__in=plan.to_delete[start : start + BATCH_SIZE],
			is_manual=False,
			is_hidden=False,
		)._raw_delete(model.objects.db)


def apply_topic_links(result, *, dry_run=False, force=False, now=None):
	"""Write a ``TopicLinksResult``, changing only what differs.

	Raises ``LabTopicRebuildAborted``, before writing anything, when more than half of either
	table's computed links would be deleted, unless ``force``. With ``dry_run`` nothing is
	written. Returns ``TopicRebuildStats``.
	"""
	now = now or timezone.now()
	stats = TopicRebuildStats()
	plans = [
		_plan_links(model, topic_attr, computed, model_stats, now)
		for (model, topic_attr), computed, model_stats in zip(
			LINK_MODELS,
			(result.subjects, result.categories),
			(stats.subjects, stats.categories),
		)
	]
	if not force:
		for plan in plans:
			if plan.computed_total and (
				len(plan.to_delete) > plan.computed_total * SAFETY_STOP_SHARE
			):
				raise LabTopicRebuildAborted(
					f"This run would delete {len(plan.to_delete)} of {plan.computed_total} "
					f"computed links in {plan.model._meta.db_table}. That usually means "
					"memberships or categories are empty or half loaded. "
					"Re-run with --force if it is intended."
				)
	if not dry_run:
		with transaction.atomic():
			for plan in plans:
				_write_links(plan)
	return stats
