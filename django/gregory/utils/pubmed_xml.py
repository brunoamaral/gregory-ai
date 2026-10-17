"""Parse PubMed EFetch XML into plain dataclasses.

Pure functions: no database access, no network. The parser is used by the PubMed
importer, the PubMed refresh and the DOI -> PMID lookup, and is tested against
XML fixtures alone (gregory/tests/fixtures/pubmed/).

PubMed's EFetch XML follows the PubMed DTD (pubmed_250101.dtd). A result set can
mix ``PubmedArticle`` and ``PubmedBookArticle``; books are skipped and counted.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from gregory.functions import normalize_orcid
from gregory.utils.doi_utils import normalize_doi

RETRACTED_PUBLICATION_UI = "D016441"
RETRACTED_PUBLICATION_NAME = "Retracted Publication"

_MONTHS = {
	name: number
	for number, name in enumerate(
		["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
		start=1,
	)
}
# A season maps to the month it starts in. Winter is January: PubMed's own
# "Winter 2025" issues are filed under the year they carry.
_SEASONS = {"winter": 1, "spring": 3, "summer": 6, "autumn": 9, "fall": 9}
_YEAR_RE = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")


class PubmedXmlError(ValueError):
	"""The document isn't a PubmedArticleSet (an error body, an empty response)."""


@dataclass
class PubmedAuthor:
	given_name: str
	family_name: str
	orcid: Optional[str] = None


@dataclass
class MeshQualifier:
	ui: str
	name: str
	major_topic: bool = False
	auto_indexed: bool = False


@dataclass
class MeshHeading:
	descriptor_ui: str
	descriptor_name: str
	major_topic: bool = False
	auto_indexed: bool = False
	qualifiers: list = field(default_factory=list)


@dataclass
class PubmedRecord:
	pmid: str
	title: str = ""
	abstract: str = ""
	doi: Optional[str] = None
	pmcid: Optional[str] = None
	journal: Optional[str] = None
	published_date: Optional[datetime] = None
	authors: list = field(default_factory=list)
	publication_types: list = field(default_factory=list)  # [(ui, name)]
	mesh: list = field(default_factory=list)
	keywords: list = field(default_factory=list)
	language: Optional[str] = None
	status: Optional[str] = None
	retracted: bool = False


@dataclass
class ParseResult:
	records: list = field(default_factory=list)
	books_skipped: int = 0


def parse_pubmed_xml(xml_bytes):
	"""Parse an EFetch ``PubmedArticleSet`` document into a :class:`ParseResult`."""
	root = ET.fromstring(xml_bytes)
	if root.tag != "PubmedArticleSet":
		# e.g. <eFetchResult><ERROR>...</ERROR></eFetchResult>, served as HTTP 200
		raise PubmedXmlError(f"expected a PubmedArticleSet, got <{root.tag}>")
	result = ParseResult()
	for element in root:
		if element.tag == "PubmedArticle":
			record = parse_article(element)
			if record is not None:
				result.records.append(record)
		elif element.tag == "PubmedBookArticle":
			result.books_skipped += 1
	return result


def _text(element):
	"""All text under ``element`` with inline markup dropped and whitespace collapsed."""
	if element is None:
		return ""
	return " ".join("".join(element.itertext()).split())


def _first_text(parent, path):
	element = parent.find(path)
	text = _text(element)
	return text or None


def parse_article(article):
	"""Parse one ``PubmedArticle`` element. None when it has no PMID."""
	citation = article.find("MedlineCitation")
	if citation is None:
		return None
	pmid = _first_text(citation, "PMID")
	if not pmid:
		return None
	body = citation.find("Article")
	pubmed_data = article.find("PubmedData")

	record = PubmedRecord(pmid=pmid, status=citation.get("Status"))
	if body is not None:
		# Plain text only. itertext() has already decoded &lt; to "<", so running the
		# result through an HTML cleaner would read "aged <or=5 years" as a tag and cut it.
		record.title = _text(body.find("ArticleTitle"))
		record.abstract = _abstract(body.find("Abstract"))
		record.journal = _first_text(body, "Journal/Title")
		record.language = _first_text(body, "Language")
		record.authors = _authors(body.find("AuthorList"))
		record.publication_types = _publication_types(body)
		record.published_date = _published_date(body, pubmed_data)
	record.doi = _doi(body, pubmed_data)
	record.pmcid = _article_id(pubmed_data, "pmc")
	record.mesh = _mesh(citation.find("MeshHeadingList"))
	record.keywords = _keywords(citation)
	record.retracted = _retracted(record.publication_types, citation)
	return record


def _abstract(abstract):
	"""Labelled sections become ``LABEL: text`` paragraphs joined by a blank line;
	the publisher's CopyrightInformation line is not part of the abstract."""
	if abstract is None:
		return ""
	paragraphs = []
	for section in abstract.findall("AbstractText"):
		text = _text(section)
		if not text:
			continue
		label = (section.get("Label") or "").strip()
		paragraphs.append(f"{label}: {text}" if label else text)
	return "\n\n".join(paragraphs)


def _article_id(pubmed_data, id_type):
	if pubmed_data is None:
		return None
	for element in pubmed_data.findall("ArticleIdList/ArticleId"):
		if element.get("IdType") == id_type:
			return _text(element) or None
	return None


def _doi(body, pubmed_data):
	doi = _article_id(pubmed_data, "doi")
	if not doi and body is not None:
		for element in body.findall("ELocationID"):
			if element.get("EIdType") == "doi":
				doi = _text(element)
				break
	return normalize_doi(doi) if doi else None


_BARE_ORCID_RE = re.compile(r"^\d{15}[\dX]$")


def _hyphenate_orcid(orcid):
	"""Some records write an ORCID without hyphens; stored ones always have them."""
	if orcid and _BARE_ORCID_RE.match(orcid):
		return "-".join(orcid[i : i + 4] for i in range(0, 16, 4))
	return orcid


def _authors(author_list):
	authors = []
	if author_list is None:
		return authors
	for author in author_list.findall("Author"):
		family = _first_text(author, "LastName")
		given = _first_text(author, "ForeName")
		# A CollectiveName has no LastName; an author with no ForeName can't be
		# matched to an Authors row, which needs both.
		if not family or not given:
			continue
		orcid = None
		for identifier in author.findall("Identifier"):
			if identifier.get("Source") == "ORCID":
				orcid = _hyphenate_orcid(normalize_orcid(_text(identifier)))
				break
		authors.append(PubmedAuthor(given_name=given, family_name=family, orcid=orcid))
	return authors


def _publication_types(body):
	types = []
	for element in body.findall("PublicationTypeList/PublicationType"):
		ui, name = element.get("UI"), _text(element)
		if ui and name:
			types.append((ui, name))
	return types


def _mesh(heading_list):
	headings = []
	if heading_list is None:
		return headings
	for heading in heading_list.findall("MeshHeading"):
		descriptor = heading.find("DescriptorName")
		if descriptor is None or not descriptor.get("UI"):
			continue
		qualifiers = [
			MeshQualifier(
				ui=q.get("UI"),
				name=_text(q),
				major_topic=q.get("MajorTopicYN") == "Y",
				auto_indexed=q.get("AutoHM") == "Y",
			)
			for q in heading.findall("QualifierName")
			if q.get("UI")
		]
		headings.append(
			MeshHeading(
				descriptor_ui=descriptor.get("UI"),
				descriptor_name=_text(descriptor),
				major_topic=descriptor.get("MajorTopicYN") == "Y",
				auto_indexed=descriptor.get("AutoHM") == "Y",
				qualifiers=qualifiers,
			)
		)
	return headings


def _keywords(citation):
	seen, keywords = set(), []
	for element in citation.findall("KeywordList/Keyword"):
		text = _text(element)
		if text and text not in seen:
			seen.add(text)
			keywords.append(text)
	return keywords


def _retracted(publication_types, citation):
	if any(
		ui == RETRACTED_PUBLICATION_UI or name == RETRACTED_PUBLICATION_NAME
		for ui, name in publication_types
	):
		return True
	return any(
		element.get("RefType") == "RetractionIn"
		for element in citation.findall("CommentsCorrectionsList/CommentsCorrections")
	)


def _month(value):
	"""A month from a number ("3", "03") or a name ("Mar", "March"); None if unreadable."""
	if not value:
		return None
	value = value.strip()
	if value.isdigit():
		number = int(value)
		return number if 1 <= number <= 12 else None
	return _MONTHS.get(value[:3].lower())


def _utc_date(year, month, day):
	try:
		return datetime(year, month or 1, day or 1, tzinfo=timezone.utc)
	except ValueError:
		# e.g. day 31 in a 30-day month: keep the month rather than lose the date
		return datetime(year, month or 1, 1, tzinfo=timezone.utc)


def _ymd(element):
	"""Year/Month/Day children of a date element as ``(year, month, day)``; year is
	None when the element has no usable year."""
	year_text = _first_text(element, "Year")
	if not year_text or not year_text.isdigit():
		return None, None, None
	month = _month(_first_text(element, "Month"))
	if month is None:
		season = (_first_text(element, "Season") or "").lower()
		month = next((m for name, m in _SEASONS.items() if season.startswith(name)), None)
	day_text = _first_text(element, "Day")
	day = int(day_text) if day_text and day_text.isdigit() else None
	return int(year_text), month, day


def _published_date(body, pubmed_data):
	"""Electronic ArticleDate, else the journal issue's PubDate, else the entrez date."""
	for element in body.findall("ArticleDate"):
		# DateType is #FIXED "Electronic" in the DTD, so it may be omitted
		if element.get("DateType", "Electronic") == "Electronic":
			year, month, day = _ymd(element)
			if year:
				return _utc_date(year, month, day)
	pub_date = body.find("Journal/JournalIssue/PubDate")
	if pub_date is not None:
		year, month, day = _ymd(pub_date)
		if year:
			return _utc_date(year, month, day)
		match = _YEAR_RE.search(_text(pub_date.find("MedlineDate")))
		if match:
			return _utc_date(int(match.group(1)), None, None)
	if pubmed_data is not None:
		for element in pubmed_data.findall("History/PubMedPubDate"):
			if element.get("PubStatus") == "entrez":
				year, month, day = _ymd(element)
				if year:
					return _utc_date(year, month, day)
	return None
