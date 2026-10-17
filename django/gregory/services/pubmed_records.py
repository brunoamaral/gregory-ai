"""Write a parsed PubMed record to an Articles row.

``apply_pubmed_record`` is the one place PubMed data reaches an article, shared by
the PubMed source importer, the PubMed refresh and the DOI -> PMID lookup.

A record never makes an existing article worse: an existing summary is replaced
only when it is empty or truncated, title and date only fill gaps, ``retracted``
only ever goes from False to True, and authors are added only to an article that
has none.
"""

import logging
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from gregory.models import (
	ArticleMeshHeading,
	Articles,
	MeshTerm,
	PublicationType,
)
from gregory.utils.authors import AMBIGUOUS, get_or_create_author
from gregory.utils.registry_utils import merge_links
from gregory.utils.text_cleaning import is_summary_truncated

logger = logging.getLogger(__name__)

PUBMED_LINK = "https://pubmed.ncbi.nlm.nih.gov/{pmid}/"


@dataclass
class ApplyResult:
	summary_replaced: bool = False
	retracted_set: bool = False
	pmid_conflict: bool = False
	authors_added: int = 0


def pubmed_link(pmid):
	return PUBMED_LINK.format(pmid=pmid)


def apply_pubmed_record(article, record, *, is_new):
	"""Apply ``record`` (a PubmedRecord) to ``article`` and return an :class:`ApplyResult`.

	``article`` must be saved: the row is locked and re-read before anything is decided.

	``is_new``: the article was just created for this record, so PubMed's title,
	abstract, DOI, PMID, PMCID and date are authoritative. Otherwise PubMed only
	fills what the article lacks (see the module docstring).
	"""
	result = ApplyResult()
	content = []  # fields saved through save(), so they reach the article history

	with transaction.atomic():
		# Lock the row and re-read it: the summary and title decisions below must see
		# what a concurrent run (or the feedreader) wrote, not a snapshot from earlier.
		list(Articles.objects.select_for_update().filter(pk=article.pk).values_list("pk"))
		article.refresh_from_db()
		if is_new:
			_apply_new(article, record, content, result)
		else:
			_apply_existing(article, record, content, result)

		if record.retracted and not article.retracted:
			article.retracted = True
			content.append("retracted")
			result.retracted_set = True

		if content:
			# last_updated is auto_now, which only fires when it is in update_fields
			article.save(update_fields=sorted(set(content) | {"last_updated"}))

		# Bookkeeping that changes on every fetch goes through update(): the history
		# would otherwise gain a row per article per refresh for no information.
		bookkeeping = {
			"keywords": record.keywords,
			"language": (record.language or article.language or "")[:3] or None,
			"pubmed_status": record.status,
			"pubmed_checked_at": timezone.now(),
		}
		Articles.objects.filter(pk=article.pk).update(**bookkeeping)
		for name, value in bookkeeping.items():
			setattr(article, name, value)

		_replace_publication_types(article, record)
		_replace_mesh(article, record)
		result.authors_added = _add_authors(article, record)
	return result


def _apply_new(article, record, content, result):
	article.title = record.title or article.title
	content.append("title")
	if record.abstract:
		article.summary = record.abstract
		content.append("summary")
	if record.published_date:
		article.published_date = record.published_date
		content.append("published_date")
	# The caller's placeholder link is replaced: a PubMed-sourced article's canonical
	# link is its PubMed page, the same form the RSS sources store.
	article.link = pubmed_link(record.pmid)
	content.append("link")
	merged = merge_links(article.links, pubmed_link(record.pmid))
	if merged != (article.links or {}):
		article.links = merged
		content.append("links")
	_claim_identifiers(article, record, content, result, fill_doi=True)


def _apply_existing(article, record, content, result):
	if record.abstract and (
		not article.summary or is_summary_truncated(article.summary)
	):
		article.summary = record.abstract
		content.append("summary")
		result.summary_replaced = True
	if record.title and not article.title:
		article.title = record.title
		content.append("title")
	if record.published_date and article.published_date is None:
		article.published_date = record.published_date
		content.append("published_date")
	_claim_identifiers(article, record, content, result, fill_doi=False)


def _claim_identifiers(article, record, content, result, *, fill_doi):
	"""Fill pmid, pmcid and (new articles only) doi, never taking one another article holds."""
	if record.pmid and not article.pmid:
		holder = (
			Articles.objects.filter(pmid=record.pmid)
			.exclude(pk=article.pk)
			.values_list("pk", flat=True)
			.first()
		)
		if holder is not None:
			result.pmid_conflict = True
			logger.warning(
				"PMID %s already belongs to article %s; not setting it on article %s "
				"(duplicate pair)",
				record.pmid,
				holder,
				article.pk,
			)
		else:
			article.pmid = record.pmid
			content.append("pmid")
	if record.pmcid and not article.pmcid:
		article.pmcid = record.pmcid
		content.append("pmcid")
	if fill_doi and record.doi and not article.doi:
		holder = (
			Articles.objects.filter(doi__iexact=record.doi)
			.exclude(pk=article.pk)
			.values_list("pk", flat=True)
			.first()
		)
		if holder is None:
			article.doi = record.doi
			content.append("doi")
		elif holder is not None:
			logger.warning(
				"DOI %s already belongs to article %s; not setting it on article %s",
				record.doi,
				holder,
				article.pk,
			)


def _upsert_terms(model, entries):
	"""Upsert rows by ``ui`` and return ``{ui: row}``. ``entries`` is ``{ui: fields}``;
	names are overwritten so NLM's renames flow through, cut to the column's length so a
	freak long name can't fail the whole record."""
	limit = model._meta.get_field("name").max_length
	entries = {ui: {**fields, "name": fields["name"][:limit]} for ui, fields in entries.items()}
	rows = {r.ui: r for r in model.objects.filter(ui__in=list(entries))}
	missing = [model(ui=ui, **fields) for ui, fields in entries.items() if ui not in rows]
	if missing:
		model.objects.bulk_create(missing, ignore_conflicts=True)
		rows = {r.ui: r for r in model.objects.filter(ui__in=list(entries))}
	renamed = []
	for ui, row in rows.items():
		if row.name != entries[ui]["name"]:
			row.name = entries[ui]["name"]
			renamed.append(row)
	if renamed:
		model.objects.bulk_update(renamed, ["name"])
	return rows


def _replace_publication_types(article, record):
	entries = {ui: {"name": name} for ui, name in record.publication_types}
	rows = _upsert_terms(PublicationType, entries) if entries else {}
	article.publication_types.set(rows.values())


def _replace_mesh(article, record):
	entries = {}
	for heading in record.mesh:
		entries[heading.descriptor_ui] = {
			"name": heading.descriptor_name,
			"kind": MeshTerm.KIND_DESCRIPTOR,
		}
		for qualifier in heading.qualifiers:
			entries[qualifier.ui] = {"name": qualifier.name, "kind": MeshTerm.KIND_QUALIFIER}
	terms = _upsert_terms(MeshTerm, entries) if entries else {}

	rows, seen = [], set()
	for heading in record.mesh:
		descriptor = terms[heading.descriptor_ui]
		pairs = [(None, heading.major_topic, heading.auto_indexed)] + [
			(terms[q.ui], q.major_topic, q.auto_indexed) for q in heading.qualifiers
		]
		for qualifier, major, auto in pairs:
			key = (descriptor.pk, qualifier.pk if qualifier else None)
			if key in seen:
				continue
			seen.add(key)
			rows.append(
				ArticleMeshHeading(
					article=article,
					descriptor=descriptor,
					qualifier=qualifier,
					major_topic=major,
					auto_indexed=auto,
				)
			)
	article.mesh_headings.all().delete()
	ArticleMeshHeading.objects.bulk_create(rows)


def _add_authors(article, record):
	"""PubMed's authors, only for an article that has none."""
	if not record.authors or article.authors.exists():
		return 0
	added = 0
	for author in record.authors:
		row, status = get_or_create_author(author.given_name, author.family_name, author.orcid)
		if status == AMBIGUOUS or row is None:
			continue
		article.authors.add(row)
		added += 1
	return added
