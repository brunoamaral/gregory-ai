"""
Service layer for merging duplicate ``Authors`` rows into a single survivor.

Used by:
  * the ``merge_authors`` management command (ORCID-driven bulk cleanup), and
  * the Django admin "Merge selected authors" action (manual, ad-hoc merges).

The merge, per removed author:
  * transfers article M2M links onto the survivor (skipping ones the survivor
    already holds) with set-based queries on the through table,
  * moves OpenAlex authorship rows onto the survivor (dropping one where the survivor
    already has a row on the same article) and recomputes the survivor's ``openalex_id``,
  * carries lab rows across (a manual lab membership wins over a computed one; a lab
    the removed author heads passes to the survivor, or merges into the survivor's own),
  * moves author-outreach rows and contact opt-outs onto the survivor,
  * unions the removed author's ``emails`` into the survivor's (case-insensitive, in order),
  * fills blank survivor fields (given_name/family_name/country/orcid_check)
    from the removed author,
  * deletes the removed author.

Does NOT change ``keep.ORCID`` — callers decide the survivor's final ORCID
value (set it on ``keep`` before calling). Assumes it runs inside
``transaction.atomic()``.
"""

import logging

from gregory.functions import normalize_orcid
from gregory.models import ArticleAuthorship, Articles
from gregory.services.labs import merge_author_lab_rows
from gregory.services.openalex_authorships import recompute_author_openalex_ids
from subscriptions.models import AuthorContactOptOut, AuthorOutreach

logger = logging.getLogger(__name__)


def _log(stdout, msg):
	"""Write to a management-command stdout if given, else to the logger."""
	if stdout is not None:
		stdout.write(msg)
	else:
		logger.info(msg)


class ConflictingOrcidError(ValueError):
	"""Raised when a group of authors selected for merge have different ORCIDs."""


class OutreachConflictError(ValueError):
	"""Raised when two authors in a merge each hold an outreach row for the same site.

	``AuthorOutreach`` has one slot per (site, author) and the rows are audit evidence of a
	one-time contact, so they are never merged or dropped: the group needs a human.
	"""


def outreach_conflict_sites(author_ids):
	"""Ids of the sites where more than one of ``author_ids`` has an outreach row."""
	rows = AuthorOutreach.objects.filter(author_id__in=list(author_ids)).values_list(
		"site_id", "author_id"
	)
	return sorted(duplicated_sites(rows))


def duplicated_sites(site_author_pairs):
	"""Pure: site ids that appear for two or more distinct authors in (site_id, author_id) pairs."""
	authors_by_site = {}
	for site_id, author_id in site_author_pairs:
		authors_by_site.setdefault(site_id, set()).add(author_id)
	return {site for site, authors in authors_by_site.items() if len(authors) > 1}


def union_emails(*lists):
	"""Concatenate email lists in order, dropping repeats (case-insensitive, first spelling wins)."""
	seen = set()
	merged = []
	for emails in lists:
		for email in emails or []:
			key = email.strip().lower() if isinstance(email, str) else repr(email)
			if not key or key in seen:
				continue
			seen.add(key)
			merged.append(email)
	return merged


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


def merge_authors(keep, remove, *, stdout=None, stats=None):
	"""Merge every author in ``remove`` into ``keep`` and delete them.

	Raises ``OutreachConflictError`` before changing anything when two of the authors hold an
	outreach row for the same site. ``stats``, when given, is a dict that gains the counts of
	what moved: ``articles``, ``authorships``, ``outreach_rows``, ``opt_outs``, ``removed``.

	Returns ``(keep, articles_transferred)``.
	"""
	remove = [a for a in remove if a.author_id != keep.author_id]
	if not remove:
		return keep, 0
	if stats is None:
		stats = {}
	for key in ("articles", "authorships", "outreach_rows", "opt_outs", "removed"):
		stats.setdefault(key, 0)

	conflicts = outreach_conflict_sites([keep.author_id] + [a.author_id for a in remove])
	if conflicts:
		raise OutreachConflictError(
			"These authors each have an outreach row for the same site "
			f"(site ids {', '.join(str(s) for s in conflicts)}); they cannot be merged."
		)

	through = Articles.authors.through
	total_transferred = 0
	for author in remove:
		# Articles: repoint the removed author's links the survivor doesn't already hold, then
		# drop the rest. Two removed authors on one article: the first repoints, the second's
		# link is then redundant and is dropped.
		keep_articles = through.objects.filter(authors_id=keep.author_id).values("articles_id")
		moved = (
			through.objects.filter(authors_id=author.author_id)
			.exclude(articles_id__in=keep_articles)
			.update(authors_id=keep.author_id)
		)
		through.objects.filter(authors_id=author.author_id).delete()
		if moved:
			_log(
				stdout,
				f"Transferred {moved} articles from {author.full_name} (ID: {author.author_id}) "
				f"to {keep.full_name} (ID: {keep.author_id}).",
			)
		total_transferred += moved

		# Authorships follow the author. Where the survivor already has a row on the same
		# article, its row stays and the removed author's is dropped with the author.
		keep_articles = ArticleAuthorship.objects.filter(author=keep).values("article_id")
		stats["authorships"] += (
			ArticleAuthorship.objects.filter(author=author)
			.exclude(article_id__in=keep_articles)
			.update(author=keep)
		)

		# Outreach rows are PROTECTed audit evidence and opt-outs would lose their author on
		# delete (SET_NULL): both move to the survivor.
		stats["outreach_rows"] += AuthorOutreach.objects.filter(author=author).update(author=keep)
		stats["opt_outs"] += AuthorContactOptOut.objects.filter(author=author).update(author=keep)

		merge_author_lab_rows(keep, author)

		keep.emails = union_emails(keep.emails, author.emails)
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
	stats["articles"] += total_transferred
	stats["removed"] += len(remove)
	return keep, total_transferred
