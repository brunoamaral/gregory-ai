"""
Service layer for merging duplicate ``Articles`` rows into a single survivor.

Two independently-created rows can silently converge on the same DOI: the
feedreader creates a DOI-less row (e.g. a BASE-search entry whose title differs
by punctuation), and weeks later ``find_doi`` fills in a DOI that another article
already holds. This module reconciles such rows without discarding curation.

Used by:
  * the ``merge_duplicate_articles`` management command (one-time / auditable
    cleanup), and
  * the DOI-write guard (``assign_doi_or_merge``) that stops new collisions at
    the moment a DOI is assigned.

The merge, per removed article:
  * unions the M2M links (sources, subjects, teams, authors, entities,
    ml_predictions) onto the survivor,
  * preserves manual relevance decisions (``ArticleSubjectRelevance``) — a
    reviewed value on the loser fills an unreviewed slot on the survivor,
  * repoints every other reverse-FK child (category assignments, trial
    references, org content, sent notifications) to the survivor, dropping a
    child only when the survivor already holds the equivalent unique row,
  * deletes the removed article,
  * recomputes the survivor's denormalized ``relevant`` flag rather than copying
    it blindly.

All callers are expected to wrap this in ``transaction.atomic()``.
"""

import logging

from django.db import IntegrityError, transaction

from gregory.models import Articles, ArticleSubjectRelevance
from gregory.relevance import recompute_article_relevance

logger = logging.getLogger(__name__)


def _log(stdout, msg):
	"""Write to a management-command stdout if given, else to the logger."""
	if stdout is not None:
		stdout.write(msg)
	else:
		logger.info(msg)


def pick_survivor(articles):
	"""Choose which article to keep from a group of duplicates.

	Preference order (most important first):
	  1. has a manual relevance decision (curation we must not discard),
	  2. has ML predictions,
	  3. earliest ``discovery_date`` (the original import),
	  4. lowest ``article_id`` (stable tie-breaker).
	"""

	def sort_key(a):
		has_manual = a.article_subject_relevances.filter(
			is_relevant__isnull=False
		).exists()
		has_ml = a.ml_predictions.exists()
		# discovery_date is auto_now_add so it is always set, but guard anyway.
		discovered = a.discovery_date
		return (
			0 if has_manual else 1,
			0 if has_ml else 1,
			discovered.timestamp() if discovered else float("inf"),
			a.article_id,
		)

	return sorted(articles, key=sort_key)[0]


def _merge_subject_relevances(keep, rem, stdout=None):
	"""Union ArticleSubjectRelevance rows, preferring a reviewed value.

	Handled explicitly (rather than by the generic reverse-FK repoint) so a
	manual review on the removed article is never dropped: a reviewed value
	fills an unreviewed slot on the survivor; the survivor's own reviewed value
	wins on a genuine conflict.
	"""
	keep_by_subject = {
		r.subject_id: r for r in keep.article_subject_relevances.all()
	}
	for rem_rel in rem.article_subject_relevances.all():
		existing = keep_by_subject.get(rem_rel.subject_id)
		if existing is None:
			rem_rel.article = keep
			rem_rel.save(update_fields=["article"])
			keep_by_subject[rem_rel.subject_id] = rem_rel
		else:
			if existing.is_relevant is None and rem_rel.is_relevant is not None:
				existing.is_relevant = rem_rel.is_relevant
				existing.save(update_fields=["is_relevant"])
				_log(
					stdout,
					f"   adopted manual relevance for subject {rem_rel.subject_id} "
					f"from article {rem.article_id}",
				)
			rem_rel.delete()


def merge_articles(keep, remove, *, stdout=None, recompute=True):
	"""Merge every article in ``remove`` into ``keep`` and delete them.

	Does NOT change ``keep.doi`` — callers decide the survivor's DOI. Assumes it
	runs inside a transaction. Returns the (refreshed) survivor.
	"""
	remove = [r for r in remove if r.article_id != keep.article_id]
	if not remove:
		return keep

	# M2M fields declared on Articles (sources, subjects, teams, authors,
	# entities, ml_predictions). team_categories is a through M2M handled below
	# via its reverse FK so the `source` (manual/automatic) column is preserved.
	m2m_fields = [
		f.name
		for f in Articles._meta.get_fields()
		if f.many_to_many and not f.auto_created
	]
	# Reverse FKs, minus the relevance relation we handle explicitly above.
	reverse_fks = [
		f
		for f in Articles._meta.get_fields()
		if f.one_to_many and f.related_model is not ArticleSubjectRelevance
	]

	for rem in remove:
		_log(stdout, f"Merging article {rem.article_id} into {keep.article_id} …")

		# 1. M2M links → add the removed article's onto the survivor.
		for fname in m2m_fields:
			related = list(getattr(rem, fname).all())
			if related:
				getattr(keep, fname).add(*related)

		# 2. Manual relevance decisions (curation-preserving union).
		_merge_subject_relevances(keep, rem, stdout=stdout)

		# 3. Every other reverse-FK child → repoint to the survivor; drop only on
		#    a unique collision. A per-child savepoint lets us recover from the
		#    IntegrityError and keep using the outer transaction.
		for rel in reverse_fks:
			accessor = rel.get_accessor_name()
			fk_name = rel.field.name
			for child in list(getattr(rem, accessor).all()):
				try:
					with transaction.atomic():
						setattr(child, fk_name, keep)
						child.save(update_fields=[fk_name])
				except IntegrityError:
					dropped_pk = child.pk
					child.delete()
					_log(
						stdout,
						f"   dropped duplicate {rel.related_model.__name__} "
						f"#{dropped_pk} (survivor already has the equivalent row)",
					)

		removed_id = rem.article_id
		removed_doi = rem.doi
		rem.delete()
		_log(
			stdout,
			f"   merged and deleted article {removed_id} (doi={removed_doi}).",
		)

	keep.refresh_from_db()
	if recompute:
		# Re-derive relevance from the unioned predictions / manual decisions
		# instead of trusting whichever row happened to survive.
		recompute_article_relevance(article_ids=[keep.article_id])
		keep.refresh_from_db()
	return keep


def assign_doi_or_merge(article, doi, *, stdout=None):
	"""Assign ``doi`` to ``article`` unless another article already holds it.

	This is the guard that closes the gap in the DOI-writing paths: instead of
	silently creating a second row with the same DOI, converging rows are merged
	into one survivor.

	Returns ``(survivor, merged)`` where ``merged`` is True when a collision was
	resolved by merging. The caller should use the returned survivor afterwards,
	as ``article`` may have been deleted.

	Wrap in ``transaction.atomic()``.
	"""
	others = list(
		Articles.objects.filter(doi__iexact=doi).exclude(pk=article.pk)
	)
	if not others:
		article.doi = doi
		article.save(update_fields=["doi"])
		return article, False

	logger.warning(
		"DOI collision: article %s would take DOI %s already held by %s — merging.",
		article.article_id,
		doi,
		[o.article_id for o in others],
	)
	group = [article] + others
	survivor = pick_survivor(group)
	losers = [a for a in group if a.article_id != survivor.article_id]

	# Delete the losers FIRST so the DOI is freed before we (re)assign it to the
	# survivor — otherwise the survivor and a loser briefly share the DOI and
	# would trip the partial unique index once it exists.
	merge_articles(survivor, losers, stdout=stdout, recompute=False)
	if not survivor.doi:
		survivor.doi = doi
		survivor.save(update_fields=["doi"])
	recompute_article_relevance(article_ids=[survivor.article_id])
	survivor.refresh_from_db()
	return survivor, True
