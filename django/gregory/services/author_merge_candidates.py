"""Author merge candidates: groups of ``Authors`` rows that share an OpenAlex author ID.

Candidates are computed live, never stored. A group is every row with the same non-blank
``Authors.openalex_id``, two rows or more. Each group is classified:

* ``clear_cut``: no ORCID or outreach conflict, every row has a family and a given name, all
  rows share the normalised family name, and every two given names agree (see
  ``given_names_agree``: "M P", "MP", "Maria Pia" and "Mariapia" agree; "Shihong" and
  "Shiliang" don't). The pipeline merges these unattended, so the rule leans to review.
* ``names_differ``: no conflict, but the names don't satisfy that rule ("SF Ahmed" and
  "Samar Farouk Ahmed").
* ``blocked``: two different ORCIDs, or two rows with an outreach row for the same site.
  Blocked groups cannot be merged from the screen or the command.

A superuser can dismiss a group ("not the same person"). The dismissal stores the sorted ids
of the group at that moment and hides the group only while its ids are still exactly those.

The classification functions are pure (they take row objects); ``load_groups`` is the one
place that queries, with a fixed number of queries however many groups there are.
"""

import logging
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce

from gregory.models import Articles, AuthorMergeDismissal, Authors, LabMembership
from gregory.services.author_merge import (
	ConflictingOrcidError,
	OutreachConflictError,
	duplicated_sites,
	merge_authors,
	shared_orcid,
)
from subscriptions.models import AuthorOutreach

logger = logging.getLogger(__name__)

CLEAR_CUT = "clear_cut"
NAMES_DIFFER = "names_differ"
BLOCKED = "blocked"
DISMISSED = "dismissed"

PAGE_SIZE = 50
# A bulk request stops at whichever of these it reaches first. A merge of a typical group
# takes tens of milliseconds, so the count is the usual limit; the time budget is the safety
# net, well under gunicorn's 300 s timeout.
BULK_BATCH_SIZE = 300
BULK_TIME_BUDGET_SECONDS = 120


def normalise_name(value):
	"""NFKD, drop combining marks, casefold, hyphens and dots to spaces, keep letters and
	spaces, collapse runs of spaces."""
	chars = []
	for ch in unicodedata.normalize("NFKD", value or ""):
		if unicodedata.category(ch) == "Mn":
			continue
		ch = ch.casefold().replace("\u0131", "i")  # dotless ı has no NFKD decomposition
		if ch in "-.":
			ch = " "
		if ch == " " or ch.isalpha():
			chars.append(ch)
	return " ".join("".join(chars).split())


@dataclass
class CandidateRow:
	author_id: int
	given_name: str
	family_name: str
	ORCID: str
	openalex_id: str
	emails: list
	article_count: int = 0
	outreach_count: int = 0
	lab: str = "-"

	@property
	def full_name(self):
		return f"{self.given_name} {self.family_name}".strip()

	@property
	def has_emails(self):
		return bool(self.emails)


@dataclass
class CandidateGroup:
	openalex_id: str
	rows: list
	status: str = CLEAR_CUT
	reason: str = ""
	dismissed: bool = False
	survivor_id: int = 0

	@property
	def ids(self):
		return tuple(sorted(r.author_id for r in self.rows))

	@property
	def total_articles(self):
		return sum(r.article_count for r in self.rows)

	@property
	def names(self):
		return " | ".join(r.full_name for r in self.rows)


def default_survivor(rows):
	"""The row with an ORCID, then the most articles, then the lowest author_id."""
	return min(rows, key=lambda r: (not r.ORCID, -r.article_count, r.author_id)).author_id


def _token_agrees(a, b):
	"""Equal, or one is a single letter the other starts with."""
	return a == b or (len(a) == 1 and b.startswith(a)) or (len(b) == 1 and a.startswith(b))


def _tokens_agree(a, b):
	"""Given-name token lists agree position by position, where a missing tail is fine
	("Maria" and "Maria Pia") and a 2-3 letter token may be glued initials ("MP" and
	"Maria Pia", "RH" and "Randy")."""
	if not a or not b:
		return True
	if _token_agrees(a[0], b[0]) and _tokens_agree(a[1:], b[1:]):
		return True
	for x, y in ((a, b), (b, a)):
		glued = x[0]
		# "Jun" must not read as the initials J, U, N: a token with a vowel counts as glued
		# initials only when there are at least two names on the other side to spell out.
		looks_like_initials = not set(glued) & set("aeiouy") or len(y) >= 2
		if 2 <= len(glued) <= 3 and looks_like_initials and all(
			y[i].startswith(glued[i]) for i in range(min(len(glued), len(y)))
		):
			if _tokens_agree(x[1:], y[len(glued):]):
				return True
	return False


def given_names_agree(a, b):
	"""Two normalised given names could be the same person's: the same letters once spaces
	are dropped ("Jiawei" and "Jia Wei"), or token by token with initials standing in for
	names. "J" and "Jun" agree; "Jun" and "Jing" don't."""
	if not a or not b:
		return False
	return a.replace(" ", "") == b.replace(" ", "") or _tokens_agree(a.split(), b.split())


def names_agree(rows):
	"""True when every row has both names, they share the family name, and every two given
	names agree."""
	families, givens = set(), []
	for row in rows:
		family = normalise_name(row.family_name)
		given = normalise_name(row.given_name)
		if not family or not given:
			return False
		families.add(family)
		givens.append(given)
	if len(families) != 1:
		return False
	unique = sorted(set(givens))
	return all(
		given_names_agree(unique[i], unique[j])
		for i in range(len(unique))
		for j in range(i + 1, len(unique))
	)


def classify_rows(rows, conflicting_sites=()):
	"""Return ``(status, reason)`` for the rows of one group. Pure."""
	try:
		shared_orcid(rows)
	except ConflictingOrcidError as exc:
		return BLOCKED, str(exc).replace("Selected authors have", "The rows have")
	if conflicting_sites:
		return BLOCKED, (
			"Two rows have an outreach row for the same site (site ids "
			f"{', '.join(str(s) for s in sorted(conflicting_sites))})."
		)
	return (CLEAR_CUT if names_agree(rows) else NAMES_DIFFER), ""


def build_groups(rows, outreach_pairs, dismissals):
	"""Group ``rows`` by OpenAlex ID and classify them. Pure.

	``outreach_pairs``: iterable of ``(site_id, author_id)``. ``dismissals``: iterable of
	``(openalex_id, author_ids)``. Groups are ordered by total article count, descending,
	then by OpenAlex ID so the order is stable.
	"""
	by_openalex = defaultdict(list)
	for row in rows:
		by_openalex[row.openalex_id].append(row)
	sites = defaultdict(list)
	for site_id, author_id in outreach_pairs:
		sites[author_id].append(site_id)
	dismissed = {(oid, tuple(sorted(ids))) for oid, ids in dismissals}

	groups = []
	for openalex_id, members in by_openalex.items():
		if len(members) < 2:
			continue
		members.sort(key=lambda r: r.author_id)
		pairs = [(site, r.author_id) for r in members for site in sites.get(r.author_id, ())]
		group = CandidateGroup(openalex_id=openalex_id, rows=members)
		group.status, group.reason = classify_rows(members, duplicated_sites(pairs))
		group.dismissed = (openalex_id, group.ids) in dismissed
		group.survivor_id = default_survivor(members)
		groups.append(group)
	groups.sort(key=lambda g: (-g.total_articles, g.openalex_id))
	return groups


def _rows_queryset(openalex_ids=None):
	"""Every row whose openalex_id is shared (or just those of ``openalex_ids``)."""
	article_counts = (
		Articles.authors.through.objects.filter(authors_id=OuterRef("pk"))
		.order_by()
		.values("authors_id")
		.annotate(n=Count("pk"))
		.values("n")
	)
	outreach_counts = (
		AuthorOutreach.objects.filter(author_id=OuterRef("pk"))
		.order_by()
		.values("author_id")
		.annotate(n=Count("pk"))
		.values("n")
	)
	queryset = (
		Authors.objects.exclude(openalex_id__isnull=True)
		.exclude(openalex_id="")
		.annotate(
			article_count=Coalesce(Subquery(article_counts, output_field=IntegerField()), 0),
			outreach_count=Coalesce(Subquery(outreach_counts, output_field=IntegerField()), 0),
		)
	)
	if openalex_ids is not None:
		return queryset.filter(openalex_id__in=list(openalex_ids))
	shared = (
		Authors.objects.exclude(openalex_id__isnull=True)
		.exclude(openalex_id="")
		.values("openalex_id")
		.annotate(n=Count("pk"))
		.filter(n__gt=1)
		.values("openalex_id")
	)
	return queryset.filter(openalex_id__in=shared)


def attach_labs(groups):
	"""Fill ``row.lab`` for the rows of ``groups`` (one query). Called on the groups of one page
	only: the lab is for display and loading it for every group would cost seconds."""
	rows = {r.author_id: r for g in groups for r in g.rows}
	if not rows:
		return
	for membership in LabMembership.objects.filter(author_id__in=list(rows)).select_related("lab__head"):
		if membership.lab_id is None:
			rows[membership.author_id].lab = "No lab (pinned)"
		else:
			rows[membership.author_id].lab = f"{membership.lab} ({membership.get_role_display().lower()})"


def load_groups(openalex_ids=None):
	"""Load, classify and order the candidate groups: at most three queries however many
	groups there are (the rows with counts, the outreach pairs, the dismissals). Rows come back
	as plain tuples, not model instances, which is what keeps 40,000 of them fast."""
	rows = [
		CandidateRow(
			author_id=author_id,
			given_name=given_name,
			family_name=family_name,
			ORCID=orcid or "",
			openalex_id=openalex_id,
			emails=emails,
			article_count=article_count,
			outreach_count=outreach_count,
		)
		for (
			author_id,
			given_name,
			family_name,
			orcid,
			openalex_id,
			emails,
			article_count,
			outreach_count,
		) in _rows_queryset(openalex_ids).values_list(
			"author_id",
			"given_name",
			"family_name",
			"ORCID",
			"openalex_id",
			"emails",
			"article_count",
			"outreach_count",
		)
	]
	outreach_pairs = []
	with_outreach = [r.author_id for r in rows if r.outreach_count]
	if with_outreach:
		outreach_pairs = list(
			AuthorOutreach.objects.filter(author_id__in=with_outreach).values_list("site_id", "author_id")
		)
	dismissals = list(AuthorMergeDismissal.objects.values_list("openalex_id", "author_ids"))
	return build_groups(rows, outreach_pairs, dismissals)


def count_by_class(groups):
	"""``{clear_cut, names_differ, blocked, dismissed}`` counts; a dismissed group counts only
	under ``dismissed``."""
	counts = {CLEAR_CUT: 0, NAMES_DIFFER: 0, BLOCKED: 0, DISMISSED: 0}
	for group in groups:
		counts[DISMISSED if group.dismissed else group.status] += 1
	return counts


def filter_groups(groups, status=CLEAR_CUT, search=""):
	"""Groups of one class (``dismissed`` selects the dismissed ones, of any class), narrowed by
	a name / ORCID / OpenAlex ID / author id search."""
	if status == DISMISSED:
		picked = [g for g in groups if g.dismissed]
	else:
		picked = [g for g in groups if not g.dismissed and g.status == status]
	raw = (search or "").strip().casefold()
	if not raw:
		return picked
	term = normalise_name(search)

	def matches(group):
		if raw in group.openalex_id.casefold() or raw in {str(i) for i in group.ids}:
			return True
		return any(
			raw in row.ORCID.casefold() or (term and term in normalise_name(row.full_name))
			for row in group.rows
		)

	return [g for g in picked if matches(g)]


@dataclass
class MergeOutcome:
	removed: int = 0
	articles: int = 0
	authorships: int = 0
	outreach_rows: int = 0
	opt_outs: int = 0
	kept: int = 0


class MergeRefused(Exception):
	"""The group can't be merged now (it changed, is blocked, or has a conflict)."""


def merge_group(openalex_id, keep_id, remove_ids, *, only=(CLEAR_CUT, NAMES_DIFFER)):
	"""Merge the chosen rows of one group into ``keep_id``, atomically.

	Reloads and re-classifies the group first, so a group that changed since it was shown is
	refused when it has become blocked or no longer fits ``only``. ``remove_ids`` must be
	members of the group other than ``keep_id``. The survivor's ORCID becomes the ORCID the
	rows share. Returns a ``MergeOutcome``.
	"""
	remove_ids = [i for i in dict.fromkeys(remove_ids) if i != keep_id]
	if not remove_ids:
		raise MergeRefused("Choose at least one row to merge into the survivor.")
	with transaction.atomic():
		locked = {
			a.author_id: a for a in Authors.objects.select_for_update().filter(pk__in=[keep_id, *remove_ids])
		}
		groups = load_groups([openalex_id])
		group = groups[0] if groups else None
		if group is None:
			raise MergeRefused("That group no longer exists.")
		if not {keep_id, *remove_ids} <= set(group.ids) or len(locked) != len(remove_ids) + 1:
			raise MergeRefused("The group changed; reload the page.")
		if group.status == BLOCKED:
			raise MergeRefused(f"Blocked: {group.reason}")
		if group.status not in only:
			raise MergeRefused("The group no longer qualifies for this merge.")
		keep = locked[keep_id]
		removing = [locked[i] for i in remove_ids]
		orcid = shared_orcid([keep, *removing])
		if orcid and keep.ORCID != orcid:
			keep.ORCID = orcid
		stats = {}
		try:
			merge_authors(keep, removing, stats=stats)
		except OutreachConflictError as exc:
			raise MergeRefused(str(exc)) from exc
	return MergeOutcome(
		removed=stats["removed"],
		articles=stats["articles"],
		authorships=stats["authorships"],
		outreach_rows=stats["outreach_rows"],
		opt_outs=stats["opt_outs"],
		kept=keep_id,
	)


@dataclass
class BulkResult:
	groups_merged: int = 0
	rows_removed: int = 0
	articles: int = 0
	failed: int = 0
	skipped: dict = field(default_factory=dict)
	remaining: int = 0


def merge_clear_cut_groups(*, limit, dry_run=False, time_budget=None, groups=None):
	"""Merge up to ``limit`` clear-cut, non-dismissed groups, each in its own transaction.

	Stops early when ``time_budget`` seconds have passed. ``remaining`` is how many clear-cut,
	non-dismissed groups were left unmerged (not counting failures). ``skipped`` counts the
	groups not eligible, by reason: ``names_differ``, ``blocked``, ``dismissed``.
	"""
	groups = load_groups() if groups is None else groups
	result = BulkResult()
	for group in groups:
		reason = DISMISSED if group.dismissed else group.status
		if reason != CLEAR_CUT:
			result.skipped[reason] = result.skipped.get(reason, 0) + 1
	todo = [g for g in groups if g.status == CLEAR_CUT and not g.dismissed]
	started = time.monotonic()
	for index, group in enumerate(todo):
		out_of_time = time_budget is not None and time.monotonic() - started > time_budget
		if index >= limit or out_of_time:
			result.remaining = len(todo) - index
			break
		if dry_run:
			result.groups_merged += 1
			result.rows_removed += len(group.rows) - 1
			continue
		try:
			outcome = merge_group(
				group.openalex_id,
				group.survivor_id,
				[i for i in group.ids if i != group.survivor_id],
				only=(CLEAR_CUT,),
			)
		except MergeRefused:
			result.failed += 1
			continue
		except Exception:  # one bad group must not stop the batch
			logger.exception("Author merge failed for OpenAlex ID %s", group.openalex_id)
			result.failed += 1
			continue
		result.groups_merged += 1
		result.rows_removed += outcome.removed
		result.articles += outcome.articles
	return result
