"""
Service layer for merging duplicate ``Authors`` rows into a single survivor.

Used by:
  * the ``merge_authors`` management command (ORCID-driven bulk cleanup), and
  * the Django admin "Merge selected authors" action (manual, ad-hoc merges).

The merge, per removed author:
  * transfers article M2M links onto the survivor (skipping ones the survivor
    already holds),
  * moves OpenAlex authorship rows onto the survivor (dropping one where the survivor
    already has a row on the same article) and recomputes the survivor's ``openalex_id``,
  * fills blank survivor fields (given_name/family_name/country/orcid_check)
    from the removed author,
  * deletes the removed author.

Does NOT change ``keep.ORCID`` — callers decide the survivor's final ORCID
value (set it on ``keep`` before calling). Assumes it runs inside
``transaction.atomic()``.
"""

import logging

from gregory.functions import normalize_orcid
from gregory.models import ArticleAuthorship
from gregory.services.openalex_authorships import recompute_author_openalex_ids

logger = logging.getLogger(__name__)


def _log(stdout, msg):
	"""Write to a management-command stdout if given, else to the logger."""
	if stdout is not None:
		stdout.write(msg)
	else:
		logger.info(msg)


class ConflictingOrcidError(ValueError):
	"""Raised when a group of authors selected for merge have different ORCIDs."""


def shared_orcid(authors):
	"""Return the bare ORCID shared by ``authors``, or None if none has one.

	ORCID is blank-tolerant: an author with no ORCID is always compatible with
	the rest of the group. Comparison is on the normalized (URL-stripped,
	uppercased) form, since stored values mix bare IDs and http/https URLs.

	Raises ConflictingOrcidError if two different non-blank ORCIDs are found.
	"""
	values = {normalize_orcid(a.ORCID) for a in authors if a.ORCID}
	values.discard(None)
	if len(values) > 1:
		raise ConflictingOrcidError(
			f"Selected authors have conflicting ORCIDs: {', '.join(sorted(values))}"
		)
	return next(iter(values), None)


def merge_authors(keep, remove, *, stdout=None):
	"""Merge every author in ``remove`` into ``keep`` and delete them.

	Returns ``(keep, articles_transferred)``.
	"""
	remove = [a for a in remove if a.author_id != keep.author_id]
	if not remove:
		return keep, 0

	total_transferred = 0
	for author in remove:
		articles = author.articles_set.all()
		if articles.exists():
			_log(
				stdout,
				f"Transferring {articles.count()} articles from "
				f"{author.full_name} (ID: {author.author_id}) to "
				f"{keep.full_name} (ID: {keep.author_id})...",
			)
			for article in articles:
				article.authors.remove(author)
				if not article.authors.filter(author_id=keep.author_id).exists():
					article.authors.add(keep)
					total_transferred += 1

		# Authorships follow the author. Where the survivor already has a row on the same
		# article, its row stays and the removed author's is dropped with the author.
		keep_articles = ArticleAuthorship.objects.filter(author=keep).values("article_id")
		ArticleAuthorship.objects.filter(author=author).exclude(article_id__in=keep_articles).update(
			author=keep
		)

		if not keep.given_name and author.given_name:
			keep.given_name = author.given_name
		if not keep.family_name and author.family_name:
			keep.family_name = author.family_name
		if not keep.country and author.country:
			keep.country = author.country
		if not keep.orcid_check and author.orcid_check:
			keep.orcid_check = author.orcid_check

	# Delete the duplicates before saving the survivor: a duplicate may hold
	# the bare ORCID the caller just set on `keep`, and ORCID is unique, so
	# saving first would raise an IntegrityError.
	for author in remove:
		_log(stdout, f"Deleting author: {author.full_name} (ID: {author.author_id})")
		author.delete()

	keep.save()
	recompute_author_openalex_ids([keep.author_id])
	keep.refresh_from_db(fields=["openalex_id"])
	return keep, total_transferred
