"""
Service layer for importing a single article by DOI.

Used by both the management command and the admin UI so logic stays in one place.
"""

import logging
from dataclasses import dataclass
from enum import Enum

from django.db import transaction
from django.utils import timezone

from gregory.classes import SciencePaper
from gregory.functions import normalize_orcid
from gregory.models import Articles, Authors, Sources
from gregory.utils.registry_utils import merge_links

logger = logging.getLogger(__name__)


class ImportStatus(str, Enum):
	CREATED = "created"
	EXISTS_BY_DOI = "exists_by_doi"
	EXISTS_BY_TITLE = "exists_by_title"
	CROSSREF_FAILURE = "crossref_failure"


@dataclass
class ImportResult:
	status: ImportStatus
	message: str
	article: Articles | None = None
	authors_added: int = 0


def normalize_doi(doi: str) -> str:
	"""Strip common URL prefixes from a DOI string."""
	prefixes = [
		"https://doi.org/",
		"http://doi.org/",
		"https://dx.doi.org/",
		"http://dx.doi.org/",
		"doi:",
	]
	doi = doi.strip()
	for prefix in prefixes:
		if doi.lower().startswith(prefix.lower()):
			doi = doi[len(prefix):]
			break
	return doi.strip()


def _process_authors(article: Articles, authors_data: list) -> int:
	"""Attach authors from CrossRef data to an article. Returns count added."""
	added = 0
	for author_data in authors_data:
		given_name = author_data.get("given")
		family_name = author_data.get("family")
		raw_orcid = author_data.get("ORCID")
		orcid = normalize_orcid(raw_orcid) if raw_orcid else None

		if not given_name or not family_name:
			logger.warning("Skipping author with missing name: %s", author_data)
			continue

		author_obj = None
		if orcid:
			author_obj, created = Authors.objects.get_or_create(
				ORCID=orcid,
				defaults={"given_name": given_name, "family_name": family_name},
			)
			if not created and (
				author_obj.given_name != given_name
				or author_obj.family_name != family_name
			):
				author_obj.given_name = given_name
				author_obj.family_name = family_name
				author_obj.save()
		else:
			try:
				author_obj = Authors.objects.get(
					given_name=given_name, family_name=family_name
				)
			except Authors.DoesNotExist:
				author_obj = Authors.objects.create(
					given_name=given_name, family_name=family_name, ORCID=orcid
				)
			except Authors.MultipleObjectsReturned:
				logger.warning(
					"Multiple authors for %s %s — skipping", given_name, family_name
				)
				continue

		if author_obj:
			article.authors.add(author_obj)
			added += 1

	return added


def create_article_from_doi(
	doi: str, source: Sources, *, skip_authors: bool = False
) -> ImportResult:
	"""
	Fetch CrossRef metadata for *doi* and create an Articles record associated
	with *source*.  Does NOT run enrichment (categories / takeaways / ML) — that
	is left to the scheduled pipeline.

	Returns an ImportResult describing what happened.
	"""
	doi = normalize_doi(doi)

	# Dedup by DOI (case-insensitive, matching the unique_article_doi constraint)
	existing = Articles.objects.filter(doi__iexact=doi).first()
	if existing:
		# Ensure source association is present
		if not existing.sources.filter(pk=source.pk).exists():
			existing.sources.add(source)
			if source.team:
				existing.teams.add(source.team)
			if source.subject:
				existing.subjects.add(source.subject)
		return ImportResult(
			status=ImportStatus.EXISTS_BY_DOI,
			message=f"Article already exists (ID {existing.article_id}): {existing.title}",
			article=existing,
		)

	# Fetch from CrossRef (+ Unpaywall via SciencePaper.refresh)
	paper = SciencePaper(doi=doi)
	result = paper.refresh()

	if not paper.title:
		error_detail = str(result) if result else "no response"
		return ImportResult(
			status=ImportStatus.CROSSREF_FAILURE,
			message=f"Could not retrieve metadata from CrossRef ({error_detail})",
		)

	# Dedup by title
	existing_by_title = Articles.objects.filter(title__iexact=paper.title).first()
	if existing_by_title:
		return ImportResult(
			status=ImportStatus.EXISTS_BY_TITLE,
			message=(
				f"Article with the same title already exists "
				f"(ID {existing_by_title.article_id}, DOI: {existing_by_title.doi})"
			),
			article=existing_by_title,
		)

	_link = paper.link or f"https://doi.org/{paper.doi}"

	with transaction.atomic():
		article = Articles.objects.create(
			title=paper.title,
			doi=paper.doi,
			link=_link,
			links=merge_links(None, _link),
			summary=paper.clean_abstract() if paper.abstract else None,
			published_date=paper.published_date,
			access=paper.access,
			publisher=paper.publisher,
			container_title=paper.journal,
			kind="science paper",
			crossref_check=timezone.now(),
			pdf_link=paper.pdf_link,
		)
		article.sources.add(source)
		if source.team:
			article.teams.add(source.team)
		if source.subject:
			article.subjects.add(source.subject)

	authors_added = 0
	if not skip_authors and paper.authors:
		authors_added = _process_authors(article, paper.authors)

	return ImportResult(
		status=ImportStatus.CREATED,
		message=f"Article created (ID {article.article_id}): {article.title}",
		article=article,
		authors_added=authors_added,
	)
