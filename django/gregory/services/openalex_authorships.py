"""Apply OpenAlex authorships to articles.

``match_authorships`` pairs each OpenAlex authorship with one of the authors already
linked to the article (it never creates authors). ``apply_openalex_work`` replaces the
article's ``ArticleAuthorship`` rows with the result. ``recompute_author_openalex_ids``
refreshes ``Authors.openalex_id`` afterwards.

The ``articles_authors`` many-to-many is never touched: ``ArticleAuthorship`` sits beside it.
"""

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from gregory.functions import normalize_orcid
from gregory.models import ArticleAuthorship, Articles, Authors, Institution

ID_BATCH_SIZE = 5000


def _norm(value):
	"""NFKD, drop accents, lower-case, hyphens to spaces, letters and spaces only."""
	s = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
	return re.sub(r"[^a-z ]", "", s.replace("-", " ")).strip()


def match_authorships(article_authors, authorships):
	"""Pair OpenAlex authorships with the article's own authors.

	``article_authors``: objects with ``author_id``, ``family_name``, ``given_name`` and
	``ORCID`` attributes. ``authorships``: ``OpenAlexAuthorship`` objects. Pure: no query.

	Rules: ORCID equality first; otherwise family name plus the first letter of the given
	name, accepted only when exactly one unused author matches; with a name of three or
	more words, a retry with the last two words as the family name. Each author is used
	once. Returns ``[(author, authorship, authorship.index)]`` in authorship order.
	"""
	article_authors = list(article_authors)
	by_orcid = {}
	for author in article_authors:
		orcid = normalize_orcid(author.ORCID)
		if orcid:
			by_orcid[orcid] = author
	by_name = defaultdict(list)
	for author in article_authors:
		by_name[(_norm(author.family_name), _norm(author.given_name)[:1])].append(author)

	used = set()
	matches = []
	for authorship in authorships:
		found = by_orcid.get(authorship.orcid) if authorship.orcid else None
		if found is None:
			words = _norm(authorship.name).split()
			if words:
				initial = words[0][:1]
				candidates = [
					a for a in by_name.get((words[-1], initial), []) if a.author_id not in used
				]
				if len(candidates) == 1:
					found = candidates[0]
				elif len(words) > 2:  # compound family names
					candidates = [
						a
						for a in by_name.get((" ".join(words[-2:]), initial), [])
						if a.author_id not in used
					]
					if len(candidates) == 1:
						found = candidates[0]
		if found is None or found.author_id in used:
			continue
		used.add(found.author_id)
		matches.append((found, authorship, authorship.index))
	return matches


@dataclass
class ApplyResult:
	matched: int = 0
	unmatched: int = 0
	institutions_created: int = 0
	author_ids: list = field(default_factory=list)


def _upsert_institutions(institutions):
	"""Create or update ``Institution`` rows by ``openalex_id``. Returns ``({id: row}, created)``."""
	wanted = {}
	for inst in institutions:
		wanted[inst.openalex_id] = inst
	if not wanted:
		return {}, 0
	existing = {i.openalex_id: i for i in Institution.objects.filter(openalex_id__in=wanted)}
	changed = []
	for openalex_id, inst in wanted.items():
		row = existing.get(openalex_id)
		if row is None:
			continue
		values = dict(
			name=inst.name, ror=inst.ror or None, country_code=inst.country_code, type=inst.type
		)
		if any(getattr(row, k) != v for k, v in values.items()):
			for k, v in values.items():
				setattr(row, k, v)
			changed.append(row)
	if changed:
		Institution.objects.bulk_update(changed, ["name", "ror", "country_code", "type"])
	new = [
		Institution(
			openalex_id=i.openalex_id,
			name=i.name,
			ror=i.ror or None,
			country_code=i.country_code,
			type=i.type,
		)
		for openalex_id, i in wanted.items()
		if openalex_id not in existing
	]
	if new:
		# ignore_conflicts: a concurrent run may have created the same institution.
		Institution.objects.bulk_create(new, ignore_conflicts=True)
	rows = {i.openalex_id: i for i in Institution.objects.filter(openalex_id__in=wanted)}
	return rows, len(new)


def apply_openalex_work(article, work, authors=None):
	"""Replace ``article``'s authorship rows with what OpenAlex says, in one transaction.

	``authors``: the article's linked authors, when the caller already has them (the
	command prefetches a whole batch in one query). Upserts the institutions, sets the
	article's OpenAlex fields and clears its back-off marker. Returns an ``ApplyResult``.
	"""
	if authors is None:
		authors = list(article.authors.all())
	matches = match_authorships(authors, work.authorships)
	result = ApplyResult(
		matched=len(matches),
		unmatched=len(work.authorships) - len(matches),
		author_ids=[author.author_id for author, _, _ in matches],
	)

	with transaction.atomic():
		institutions, result.institutions_created = _upsert_institutions(
			inst for _, authorship, _ in matches for inst in authorship.institutions
		)
		# Authors who had a row before but have none now still need their OpenAlex ID
		# recomputed.
		previous = set(
			ArticleAuthorship.objects.filter(article=article).values_list("author_id", flat=True)
		)
		result.author_ids = sorted(set(result.author_ids) | previous)
		ArticleAuthorship.objects.filter(article=article).delete()

		rows = [
			ArticleAuthorship(
				article=article,
				author=author,
				position=authorship.position,
				author_index=index,
				is_corresponding=authorship.is_corresponding,
				openalex_author_id=authorship.openalex_author_id,
				raw_affiliation=authorship.raw_affiliation,
			)
			for author, authorship, index in matches
		]
		ArticleAuthorship.objects.bulk_create(rows)

		through = ArticleAuthorship.institutions.through
		links = [
			through(articleauthorship_id=row.pk, institution_id=institutions[inst.openalex_id].pk)
			for row, (_, authorship, _) in zip(rows, matches)
			for inst in authorship.institutions
		]
		if links:
			through.objects.bulk_create(links)

		# Bookkeeping goes through update(): it changes on every fetch, and a save()
		# would add an article history row each time for no information.
		bookkeeping = dict(
			openalex_id=work.openalex_id or None,
			openalex_checked_at=timezone.now(),
			openalex_next_check=None,
			openalex_attempts=0,
		)
		Articles.objects.filter(pk=article.pk).update(**bookkeeping)
		for name, value in bookkeeping.items():
			setattr(article, name, value)
	return result


def recompute_author_openalex_ids(author_ids):
	"""Set ``Authors.openalex_id`` to the author's most frequent ``openalex_author_id``.

	A tie goes to the most recent article. An author with no authorship gets NULL.
	Batched queries and one ``bulk_update`` per batch. Returns the number of rows changed.
	"""
	author_ids = sorted(set(author_ids))
	changed = 0
	for start in range(0, len(author_ids), ID_BATCH_SIZE):
		chunk = author_ids[start : start + ID_BATCH_SIZE]
		counts = defaultdict(Counter)
		latest = defaultdict(dict)  # author -> openalex id -> (published timestamp, article id)
		rows = ArticleAuthorship.objects.filter(author_id__in=chunk).exclude(openalex_author_id="")
		for author_id, oa_id, published, article_id in rows.values_list(
			"author_id", "openalex_author_id", "article__published_date", "article_id"
		):
			counts[author_id][oa_id] += 1
			key = (published.timestamp() if published else 0, article_id)
			if oa_id not in latest[author_id] or key > latest[author_id][oa_id]:
				latest[author_id][oa_id] = key
		to_update = []
		for author in Authors.objects.filter(pk__in=chunk).only("author_id", "openalex_id"):
			tally = counts.get(author.author_id)
			best = (
				max(tally, key=lambda oa: (tally[oa], latest[author.author_id][oa]))
				if tally
				else None
			)
			if author.openalex_id != best:
				author.openalex_id = best
				to_update.append(author)
		if to_update:
			Authors.objects.bulk_update(to_update, ["openalex_id"])
			changed += len(to_update)
	return changed
