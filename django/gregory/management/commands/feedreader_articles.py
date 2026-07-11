import logging
from gregory.management.base import GregoryBaseCommand
from gregory.models import Articles, Sources, Authors
from gregory.functions import normalize_orcid
from crossref.restful import Works, Etiquette
from dateutil.parser import parse
from dateutil.tz import gettz
from django.core.exceptions import MultipleObjectsReturned
from django.db import transaction
from django.utils import timezone
from gregory.classes import SciencePaper
from gregory.services.article_merge import assign_doi_or_merge
from gregory.utils.registry_utils import merge_links
from sitesettings.models import CustomSetting
import feedparser
import gregory.functions as greg
import os
import pytz
import re
import requests
from abc import ABC, abstractmethod
from typing import Optional


class FeedProcessor(ABC):
	"""Abstract base class for RSS feed processors."""

	def __init__(self, command_instance):
		self.command = command_instance

	# Inline tags whose markup carries meaning and should be preserved in titles
	SEMANTIC_TITLE_TAGS = {"sub", "sup", "i", "b", "em", "strong"}

	@staticmethod
	def clean_title(title: Optional[str]) -> Optional[str]:
		"""Normalize a feed title before storage.

		Publisher feeds (notably PubMed/Wiley) embed inline markup and
		pretty-printed newlines/indentation inside <title>. We unescape HTML
		entities, keep semantically meaningful inline tags (sub, sup, i, b,
		em, strong) but strip presentational/JATS tags (e.g. <scp>, <jats:*>)
		while preserving their text, drop tag attributes, and collapse runs of
		whitespace to single spaces.
		"""
		if not title:
			return title
		from bs4 import BeautifulSoup
		import html

		title = html.unescape(title)
		soup = BeautifulSoup(title, "html.parser")
		for tag in soup.find_all(True):
			if tag.name in FeedProcessor.SEMANTIC_TITLE_TAGS:
				tag.attrs = {}
			else:
				tag.unwrap()
		# formatter=None keeps entities unescaped (e.g. bare &) so the stored
		# title matches the human-readable form; str(soup) would re-encode & -> &amp;.
		return " ".join(soup.decode(formatter=None).split())

	@abstractmethod
	def can_process(self, source_link: str) -> bool:
		"""Check if this processor can handle the given source link."""
		pass

	@abstractmethod
	def extract_summary(self, entry: dict) -> str:
		"""Extract summary from feed entry."""
		pass

	@abstractmethod
	def extract_doi(self, entry: dict) -> str:
		"""Extract DOI from feed entry."""
		pass

	def extract_basic_fields(self, entry: dict) -> dict:
		"""Extract common fields that are the same across all feed types."""
		# Try multiple date fields in order of preference
		date_string = (
			entry.get("published")
			or entry.get("prism_coverdate")
			or entry.get("updated")
			or entry.get("date")
			or entry.get("prism_publicationdate")
		)

		# A missing date must not cost us the article: ingest with
		# published_date=None and let update_articles_info fill the real date
		# from CrossRef downstream.
		published_date = None
		if date_string:
			published_date = parse(
				date_string, tzinfos=self.command.tzinfos
			).astimezone(pytz.utc)
		else:
			logging.warning(
				f"No date field in feed entry '{entry.get('title', 'Unknown')}'; "
				"ingesting without a published date."
			)

		return {
			"title": self.clean_title(entry["title"]),
			"link": greg.remove_utm(entry["link"]),
			"published_date": published_date,
		}

	def should_include_article(self, entry: dict, source: "Sources") -> bool:
		"""Check if article should be included based on keyword filtering."""
		if not source.keyword_filter:
			return True  # No filter, include all articles

		# Get text content to search
		# Strip inline markup and collapse whitespace before matching so tag
		# wrappers (e.g. <scp>...) and pretty-printed newlines in feed titles
		# don't split keywords and cause false negatives.
		raw_title = entry.get("title", "") or ""
		title = " ".join(re.sub(r"<[^>]+>", " ", raw_title).split()).lower()
		summary = self.extract_summary(entry).lower()
		search_text = f"{title} {summary}"

		# Parse keywords from the filter string
		keywords = self._parse_keyword_filter(source.keyword_filter)

		# Check if any keyword matches
		for keyword in keywords:
			if keyword.lower() in search_text:
				return True

		return False

	def _parse_keyword_filter(self, keyword_filter: str) -> list:
		"""Parse keyword filter string into individual keywords and phrases."""
		if not keyword_filter:
			return []

		keywords = []

		# Split by commas first to preserve order
		parts = [part.strip() for part in keyword_filter.split(",")]

		for part in parts:
			if not part:
				continue

			# Check if part is a quoted phrase
			if part.startswith('"') and part.endswith('"') and len(part) > 1:
				# Remove quotes and add the phrase
				keywords.append(part[1:-1])
			else:
				# Regular keyword
				keywords.append(part)

		return keywords


class PubMedFeedProcessor(FeedProcessor):
	"""Processor for PubMed RSS feeds."""

	def can_process(self, source_link: str) -> bool:
		return "pubmed" in source_link.lower()

	def extract_summary(self, entry: dict) -> str:
		"""Extract summary with PubMed-specific priority."""
		summary = entry.get("summary", "")
		if hasattr(entry, "summary_detail"):
			summary = entry["summary_detail"]["value"]
		if hasattr(entry, "content"):
			summary = entry["content"][0]["value"]
		return summary

	def extract_doi(self, entry: dict) -> str:
		"""Extract DOI from PubMed feed entry."""
		if entry.get("dc_identifier", "").startswith("doi:"):
			return entry["dc_identifier"].replace("doi:", "")
		return None


class FasebFeedProcessor(FeedProcessor):
	"""Processor for FASEB RSS feeds."""

	def can_process(self, source_link: str) -> bool:
		return "faseb" in source_link.lower()

	def extract_summary(self, entry: dict) -> str:
		"""Extract summary for FASEB feeds."""
		summary = entry.get("summary", "")
		if hasattr(entry, "summary_detail"):
			summary = entry["summary_detail"]["value"]
		return summary

	def extract_doi(self, entry: dict) -> str:
		"""Extract DOI from FASEB feed entry."""
		return entry.get("prism_doi", "")


class BioRxivFeedProcessor(FeedProcessor):
	"""Processor for bioRxiv and medRxiv RSS feeds with keyword filtering support."""

	def can_process(self, source_link: str) -> bool:
		link = source_link.lower()
		return "biorxiv" in link or "medrxiv" in link

	def extract_summary(self, entry: dict) -> str:
		"""Extract summary for bioRxiv and medRxiv feeds."""
		# Prefer description field for bioRxiv/medRxiv
		summary = entry.get("description", "")
		if not summary and hasattr(entry, "summary_detail"):
			summary = entry["summary_detail"].get("value", "") or ""
		elif not summary:
			summary = entry.get("summary", "")
		return summary or ""

	def extract_doi(self, entry: dict) -> str:
		"""Extract DOI from bioRxiv or medRxiv feed entry."""
		# bioRxiv and medRxiv use dc:identifier with doi: prefix
		dc_identifier = entry.get("dc_identifier", "")
		if dc_identifier.startswith("doi:"):
			return dc_identifier.replace("doi:", "")
		return None


class NatureFeedProcessor(FeedProcessor):
	"""Processor for Nature.com RSS feeds."""

	def can_process(self, source_link: str) -> bool:
		return "nature.com" in source_link.lower()

	def extract_summary(self, entry: dict) -> str:
		"""Extract summary for Nature feeds - usually empty in Nature RSS."""
		summary = entry.get("summary", "")
		if hasattr(entry, "summary_detail"):
			summary = entry["summary_detail"]["value"]
		return summary

	def extract_doi(self, entry: dict) -> str:
		"""Extract DOI from Nature feed entry link."""
		link = entry.get("link", "")
		if not link:
			return None

		# Nature links are in format: https://www.nature.com/articles/s41467-025-61751-9
		# Extract the partial DOI from the link and construct full DOI
		if "/articles/" in link:
			try:
				partial_doi = link.split("/articles/")[-1]
				# Remove any query parameters or fragments
				partial_doi = partial_doi.split("?")[0].split("#")[0]
				# Construct full DOI with Nature prefix
				if partial_doi:
					return f"10.1038/{partial_doi}"
			except (IndexError, ValueError) as e:
				logging.debug(f"Failed to extract DOI from Nature link '{link}': {e}")
				pass

		return None


class DefaultFeedProcessor(FeedProcessor):
	"""Default processor for generic RSS feeds."""

	def can_process(self, source_link: str) -> bool:
		return True  # Always can process as fallback

	def extract_summary(self, entry: dict) -> str:
		"""Extract summary for generic feeds."""
		summary = entry.get("summary", "")
		if hasattr(entry, "summary_detail"):
			summary = entry["summary_detail"]["value"]
		return summary

	def extract_doi(self, entry: dict) -> str:
		"""Generic DOI extraction - returns None as most feeds don't have DOI."""
		return None


class PNASFeedProcessor(FeedProcessor):
	"""Processor for PNAS RSS 1.0/RDF format feeds."""

	def can_process(self, source_link: str) -> bool:
		return "pnas.org" in source_link.lower()

	def extract_summary(self, entry: dict) -> str:
		"""Extract summary from PNAS feed entry."""
		summary = entry.get("description", "")

		# PNAS descriptions typically have the format:
		# "Proceedings of the National Academy of Sciences, Volume X, Issue Y, Month Year. <br/>SignificanceText..."
		if summary and "<br/>" in summary:
			# Extract only the content after the <br/> tag which contains the actual abstract
			parts = summary.split("<br/>")
			if len(parts) > 1 and parts[1].strip():
				return parts[1].strip()

		return summary

	def extract_doi(self, entry: dict) -> str:
		"""Extract DOI from PNAS feed entry."""
		# PNAS provides DOI in dc:identifier field with 'doi:' prefix
		dc_identifier = entry.get("dc_identifier", "")
		if dc_identifier.startswith("doi:"):
			return dc_identifier.replace("doi:", "")

		# Alternatively, check prism:doi if available
		prism_doi = entry.get("prism_doi", "")
		if prism_doi:
			return prism_doi

		return None


class SagePublicationsFeedProcessor(FeedProcessor):
	"""Processor for SAGE Publications RSS feeds (RDF format) with keyword filtering support."""

	def can_process(self, source_link: str) -> bool:
		return (
			"sagepub.com" in source_link.lower()
			or "journals.sagepub.com" in source_link.lower()
		)

	def extract_summary(self, entry: dict) -> str:
		"""Extract summary from SAGE Publications feed entry."""
		# Try multiple content fields in order of preference
		# SAGE uses content:encoded for the full description
		summary = entry.get("content_encoded", "")
		used_field = "content_encoded" if summary else None

		if not summary:
			summary = entry.get("description", "")
			used_field = "description" if summary else None

		if not summary and hasattr(entry, "summary_detail"):
			summary = entry["summary_detail"].get("value", "")
			used_field = "summary_detail" if summary else None

		if not summary:
			summary = entry.get("summary", "")
			used_field = "summary" if summary else None

		# Clean up HTML tags and formatting for better readability
		if summary:
			# Remove HTML tags
			import re

			summary = re.sub(r"<[^>]+>", "", summary)
			# Clean up extra whitespace
			summary = " ".join(summary.split())

			# For description field: Remove SAGE metadata at the beginning
			# For content_encoded field: Keep the metadata as it's part of professional publication header
			if used_field == "description":
				# SAGE descriptions often have volume/issue info at the beginning
				# Extract content after common patterns like "Volume X, Issue Y, Month Year. "
				if "Volume " in summary and "Issue " in summary:
					# Try to find the start of actual content after metadata
					parts = summary.split(". ")
					if len(parts) > 1:
						# Skip the first part which is usually metadata
						summary = ". ".join(parts[1:])

		return summary or ""

	def extract_doi(self, entry: dict) -> str:
		"""Extract DOI from SAGE Publications feed entry."""
		# SAGE uses dc:identifier with 'doi:' prefix
		dc_identifier = entry.get("dc_identifier", "")
		if dc_identifier and dc_identifier.startswith("doi:"):
			return dc_identifier.replace("doi:", "")

		# Also check prism:doi if available
		prism_doi = entry.get("prism_doi", "")
		if prism_doi:
			return prism_doi

		# Fallback: try to extract DOI from the link
		link = entry.get("link", "")
		if link and "doi/abs/10.1177/" in link:
			try:
				# Extract DOI from URL pattern like:
				# https://journals.sagepub.com/doi/abs/10.1177/21582440251334940
				doi_match = re.search(r"doi/abs/(10\.1177/[^?&]+)", link)
				if doi_match:
					return doi_match.group(1)
			except (IndexError, ValueError) as e:
				logging.debug(f"Failed to extract DOI from Nature link '{link}': {e}")

		return None


class SpringerLinkFeedProcessor(FeedProcessor):
	"""Processor for Springer Link RSS feeds (link.springer.com)."""

	def can_process(self, source_link: str) -> bool:
		return "link.springer.com" in source_link.lower()

	def extract_summary(self, entry: dict) -> str:
		"""Extract summary from Springer Link feed entry.

		Springer uses <description> with HTML tags (typically <p> tags).
		We strip HTML tags but preserve HTML entities, then trim whitespace.
		"""
		summary = entry.get("description", "") or entry.get("summary", "")

		if not summary:
			return ""

		# Strip HTML tags but preserve HTML entities
		summary = re.sub(r"<[^>]+>", "", summary)

		# Trim whitespace (including newlines and multiple spaces)
		summary = " ".join(summary.split())

		return summary

	def extract_doi(self, entry: dict) -> str:
		"""Extract DOI from Springer Link feed entry.

		Springer uses <guid> containing the DOI directly (e.g., 10.1007/s00332-025-10234-8).
		We validate that it starts with '10.' before accepting it.
		"""
		guid = entry.get("id", "") or entry.get("guid", "")

		if not guid:
			return None

		# Validate DOI format (should start with 10.)
		if guid.startswith("10."):
			return guid

		return None


class BaseSearchFeedProcessor(FeedProcessor):
	"""Processor for BASE (Bielefeld Academic Search Engine) RSS feeds."""

	def can_process(self, source_link: str) -> bool:
		return "base-search.net" in source_link.lower()

	def extract_summary(self, entry: dict) -> str:
		"""Extract summary from BASE feed entry."""
		# BASE uses 'summary' field
		summary = entry.get("summary", "")

		if not summary and hasattr(entry, "summary_detail"):
			summary = entry["summary_detail"].get("value", "")

		# BASE summaries are often truncated but we accept them as-is
		# Clean up extra whitespace
		if summary:
			summary = " ".join(summary.split())

		return summary or ""

	def extract_doi(self, entry: dict) -> str:
		"""Extract DOI from BASE feed entry."""
		# BASE uses dc:relation field with format: "doi:10.1177/... ; PMID"
		dc_relation = entry.get("dc_relation", "")

		if not dc_relation:
			return None

		# Split by semicolon to handle multiple values
		parts = re.split(r"\s*;\s*", dc_relation)

		for part in parts:
			part = part.strip()

			# Method 1: Check for 'doi:' prefix (most common in BASE)
			if part.lower().startswith("doi:"):
				doi = part[4:].strip()
				# Validate it looks like a DOI (starts with 10.)
				if doi.startswith("10."):
					return doi

			# Method 2: Check for doi.org URL
			if "doi.org" in part.lower():
				doi_match = re.search(r"doi\.org/(10\.\d+/[^\s;]+)", part)
				if doi_match:
					return doi_match.group(1)

		return None


class Command(GregoryBaseCommand):
	help = "Fetches and updates articles and trials from RSS feeds."

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.feed_processors = [
			PubMedFeedProcessor(self),
			FasebFeedProcessor(self),
			BioRxivFeedProcessor(self),
			PNASFeedProcessor(self),
			SagePublicationsFeedProcessor(self),
			NatureFeedProcessor(self),
			SpringerLinkFeedProcessor(self),
			BaseSearchFeedProcessor(self),
			DefaultFeedProcessor(self),  # Always last as fallback
		]

	def handle(self, *args, **options):
		self.setup()
		self.update_articles_from_feeds()

	def setup(self):
		self.SITE = CustomSetting.objects.get(
			site__domain=os.environ.get("DOMAIN_NAME")
		)
		self.CLIENT_WEBSITE = f"https://{self.SITE.site.domain}/"
		my_etiquette = Etiquette(
			self.SITE.title, "v8", self.CLIENT_WEBSITE, self.SITE.admin_email
		)
		self.works = Works(etiquette=my_etiquette)
		self.tzinfos = {
			"EDT": gettz("America/New_York"),
			"EST": gettz("America/New_York"),
		}

	def fetch_feed(self, link, ignore_ssl):
		if not ignore_ssl:
			return feedparser.parse(link)
		else:
			response = requests.get(link, verify=False, timeout=30)
			return feedparser.parse(response.content)

	def handle_database_error(self, action, error):
		"""Generic error handler for database operations."""
		self.log(f"An error occurred during {action}: {str(error)}", level=2)

	def get_feed_processor(self, source_link: str) -> FeedProcessor:
		"""Get the appropriate feed processor for the given source link."""
		for processor in self.feed_processors:
			if processor.can_process(source_link):
				return processor
		# This should never happen since DefaultFeedProcessor always returns True
		return self.feed_processors[-1]

	def update_articles_from_feeds(self):
		# Per-source failures are isolated but recorded here so callers that
		# need a hard failure signal can inspect them after the run.
		self.fetch_errors = []
		sources = Sources.objects.filter(
			method="rss", source_for="science paper", active=True
		)
		for source in sources:
			self.log(f"# Processing articles from {source}", level=1)
			# One broken source (timeout, DNS, SSL) must not abort the whole run
			# and silently skip every source after it in the loop.
			try:
				feed = self.fetch_feed(source.link, source.ignore_ssl)
			except Exception as e:
				self.fetch_errors.append(f"{source.name}: {e}")
				self.log(
					f"Failed to fetch feed for source '{source.name}' ({source.link}): {e}. "
					"Skipping this source.",
					level=1,
					style_func=self.style.ERROR,
				)
				continue
			processor = self.get_feed_processor(source.link)

			for entry in feed["entries"]:
				try:
					# Check if the article should be included based on keyword filtering
					prefetched = None
					if hasattr(
						processor, "should_include_article"
					) and not processor.should_include_article(entry, source):
						# Sparse feeds (e.g. Nature) ship empty summaries, so
						# the filter above only saw the title; give the entry a
						# second chance against its CrossRef abstract.
						included, prefetched = self.deferred_keyword_check(
							entry, source, processor
						)
						if not included:
							self.log(
								f"  ➡️  Excluded by keyword filter: {entry.get('title', 'Unknown')}",
								level=2,
							)
							continue

					self.process_feed_entry(
						entry, source, processor, prefetched=prefetched
					)
				except Exception as e:
					self.log(
						f"Error processing entry '{entry.get('title', 'Unknown')}': {str(e)}",
						level=2,
					)
					continue

	def deferred_keyword_check(
		self, entry: dict, source: Sources, processor: FeedProcessor
	) -> tuple[bool, object]:
		"""Re-run the keyword decision against the CrossRef abstract.

		Only applies when the feed summary is empty (the initial filter had
		nothing but the title to search) and the entry carries a DOI. Returns
		(included, prefetched) — `prefetched` is the (SciencePaper,
		refresh_result) pair, handed downstream so the entry is not charged a
		second CrossRef call. When CrossRef fails or has no abstract the
		original exclusion stands (known bounded waste: excluded entries are
		re-checked while they remain in the feed window — see
		CROSSREF-REJECTION-CACHE-NOTE.md at the repo root).
		"""
		if not source.keyword_filter:
			return False, None
		if (processor.extract_summary(entry) or "").strip():
			# The filter already saw a real summary; the exclusion is final.
			return False, None
		doi = processor.extract_doi(entry)
		if not doi:
			return False, None

		crossref_paper = SciencePaper(doi=doi)
		refresh_result = crossref_paper.refresh()
		if SciencePaper.is_crossref_failed(refresh_result):
			return False, None
		abstract = (crossref_paper.abstract or "").strip()
		if not abstract:
			return False, None

		abstract = SciencePaper.clean_abstract(abstract=abstract) or ""
		raw_title = entry.get("title", "") or ""
		title = " ".join(re.sub(r"<[^>]+>", " ", raw_title).split())
		search_text = f"{title} {abstract}".lower()
		for keyword in processor._parse_keyword_filter(source.keyword_filter):
			if keyword.lower() in search_text:
				self.log(
					f"  Keyword matched via CrossRef abstract for DOI {doi}: "
					f"{entry.get('title', 'Unknown')}",
					level=2,
				)
				return True, (crossref_paper, refresh_result)
		return False, None

	def process_feed_entry(
		self,
		entry: dict,
		source: Sources,
		processor: FeedProcessor,
		prefetched=None,
	):
		"""Process a single feed entry."""
		# Extract basic fields common to all feed types
		basic_fields = processor.extract_basic_fields(entry)
		title = basic_fields["title"]
		link = basic_fields["link"]
		published_date = basic_fields["published_date"]

		# Extract feed-specific fields
		raw_summary = processor.extract_summary(entry)
		doi = processor.extract_doi(entry)

		# Clean and store the original feed summary
		feed_summary = (
			SciencePaper.clean_abstract(abstract=raw_summary) if raw_summary else ""
		)

		if doi:
			self.process_article_with_doi(
				doi,
				title,
				feed_summary,
				link,
				published_date,
				source,
				prefetched=prefetched,
			)
		else:
			self.process_article_without_doi(
				title, feed_summary, link, published_date, source
			)

	def process_article_with_doi(
		self,
		doi: str,
		title: str,
		feed_summary: str,
		link: str,
		published_date,
		source: Sources,
		prefetched=None,
	):
		"""Process an article that has a DOI.

		The existence check runs BEFORE any CrossRef call: most entries in
		every feed are already ingested, and refreshing them cost a CrossRef +
		Unpaywall round-trip per entry per run for nothing. An existing
		article that already has CrossRef data on file only gets its
		feed-level facts merged; the full CrossRef path runs for new articles
		and for existing ones never checked against CrossRef.
		"""
		existing = self.find_existing_article(doi, title, link)
		if existing is not None and existing.crossref_check:
			self.merge_feed_entry_into_article(
				existing, doi, feed_summary, link, published_date, source
			)
			return

		# `prefetched` carries the CrossRef result already fetched by the
		# deferred keyword check, so the entry does not pay for a second call.
		if prefetched is not None:
			crossref_paper, refresh_result = prefetched
		else:
			crossref_paper = SciencePaper(doi=doi)
			refresh_result = crossref_paper.refresh()

		# Determine article data based on CrossRef success/failure
		article_data = self.get_article_data_with_crossref(
			crossref_paper, refresh_result, title, feed_summary, doi
		)

		# Create or update article
		science_paper, created, crossref_was_updated = self.create_or_update_article(
			doi=doi,
			title=article_data["title"],
			summary=article_data["summary"],
			link=link,
			published_date=published_date,
			source=source,
			container_title=article_data["container_title"],
			publisher=article_data["publisher"],
			access=article_data["access"],
			crossref_check=article_data["crossref_check"],
			pdf_link=article_data["pdf_link"],
		)

		# Process authors if:
		# 1. CrossRef data is available AND
		# 2. (Article was created OR CrossRef data was updated for existing article)
		if not SciencePaper.is_crossref_failed(refresh_result) and (
			created or crossref_was_updated
		):
			self.log(
				f" Processing authors for {'new' if created else 'updated'} article: {science_paper.title}",
				level=2,
			)
			self.process_authors(crossref_paper, science_paper)

	def merge_feed_entry_into_article(
		self, article: Articles, doi: str, feed_summary: str, link: str, published_date, source: Sources
	):
		"""Merge feed-level facts into an article whose CrossRef data is on file.

		CrossRef-derived fields (title, summary, journal, publisher, access)
		are authoritative and left alone; the feed only contributes its URL,
		a summary/date when none exists yet, and the source relationships.
		"""
		# The DOI-first lookup in find_existing_article matches case-sensitively,
		# so a case-only variant of an existing DOI can reach here on a row found
		# by link/title. Route the assignment through the collision guard so it
		# merges rather than tripping the case-insensitive uniqueness constraint.
		if doi and not article.doi:
			with transaction.atomic():
				article, _merged = assign_doi_or_merge(article, doi)

		changed_fields = []
		merged = merge_links(article.links, link)
		if merged != (article.links or {}):
			article.links = merged
			changed_fields.append("links")
		if feed_summary and not article.summary:
			article.summary = feed_summary
			changed_fields.append("summary")
		if published_date is not None and article.published_date is None:
			article.published_date = published_date
			changed_fields.append("published_date")
		if changed_fields:
			article.save(update_fields=changed_fields)
			self.log(
				f" Merged feed data into existing article ({', '.join(changed_fields)}): {article.title}",
				level=2,
			)
		self.add_article_relationships(article, source)

	def process_article_without_doi(
		self, title: str, feed_summary: str, link: str, published_date, source: Sources
	):
		"""Process an article that doesn't have a DOI."""
		self.log("No DOI, trying to create article", level=2)

		# Log potential summary truncation issues
		if 20 < len(feed_summary) < 500:
			self.log(
				f"Warning: Potentially truncated summary for title '{title}': {len(feed_summary)} characters",
				level=2,
			)

		# Create or update article (no CrossRef data, so crossref_was_updated will be False)
		science_paper, created, crossref_was_updated = self.create_or_update_article(
			doi=None,
			title=title,
			summary=feed_summary,
			link=link,
			published_date=published_date,
			source=source,
			crossref_check=None,
		)

	def get_article_data_with_crossref(
		self,
		crossref_paper: SciencePaper,
		refresh_result,
		title: str,
		feed_summary: str,
		doi: str,
	) -> dict:
		"""Get article data based on CrossRef lookup results."""
		if SciencePaper.is_crossref_failed(refresh_result):
			self.log(
				f"  ⚠️  CrossRef lookup failed for DOI {doi}: {refresh_result}", level=2
			)
			return {
				"title": title,
				"summary": feed_summary,
				"container_title": None,
				"publisher": None,
				"access": None,
				"crossref_check": None,
				"pdf_link": None,
			}
		else:
			# CrossRef data available, use it with fallbacks
			crossref_title = crossref_paper.title if crossref_paper.title else title

			# Use crossref abstract if available, otherwise use feed summary
			if crossref_paper.abstract and crossref_paper.abstract.strip():
				summary = SciencePaper.clean_abstract(abstract=crossref_paper.abstract)
			else:
				summary = feed_summary

			# Log potential summary truncation issues
			if 20 < len(summary) < 500:
				self.log(
					f"  ⚠️  Potentially truncated summary for DOI {doi}: {len(summary)} characters",
					level=2,
				)

			return {
				"title": crossref_title,
				"summary": summary,
				"container_title": crossref_paper.journal,
				"publisher": crossref_paper.publisher,
				"access": crossref_paper.access,
				"crossref_check": timezone.now(),
				"pdf_link": crossref_paper.pdf_link,
			}

	def find_existing_article(self, doi: str, title: str, link: str):
		"""Locate the article this feed entry refers to, DOI-first.

		Lookup order:
		1. DOI — the only globally unique key we have. Matched case-insensitively
		   to line up with the unique_article_doi constraint, so a case-only DOI
		   variant resolves to the existing row instead of creating a collision.
		2. Link — an article's first-seen URL is stable; this also catches rows
		   ingested before title cleaning existed (PR #739).
		3. Cleaned title — but ONLY when the incoming entry has no DOI or the
		   candidate has no DOI. When both sides carry different non-null DOIs
		   they are different papers (errata, corrections, preprint vs
		   published all collide on title) and a new article must be created
		   instead of absorbing the entry into the wrong row.
		"""
		if doi:
			article = Articles.objects.filter(doi__iexact=doi).first()
			if article:
				return article

		if link:
			article = Articles.objects.filter(link=link).first()
			if article:
				return article

		article = Articles.objects.filter(title=title).first()
		if article:
			if doi and article.doi and article.doi.lower() != doi.lower():
				self.log(
					f"  Title matches article {article.article_id} but DOIs differ "
					f"({article.doi} vs {doi}); creating a new article.",
					level=2,
				)
				return None
			return article

		return None

	def create_or_update_article(
		self,
		doi: str,
		title: str,
		summary: str,
		link: str,
		published_date,
		source: Sources,
		container_title=None,
		publisher=None,
		access=None,
		crossref_check=None,
		pdf_link=None,
	) -> tuple[Articles, bool, bool]:
		"""Create a new article or update existing one. Returns (article, created, crossref_updated)."""
		existing_article = self.find_existing_article(doi, title, link)

		crossref_was_updated = False

		if existing_article:
			science_paper = existing_article
			created = False

			# An article first seen via a DOI-less feed gains its DOI here; the
			# find_existing_article guard already ruled out conflicting DOIs.
			if doi and not science_paper.doi:
				science_paper.doi = doi
				science_paper.save(update_fields=["doi"])
				self.log(
					f"  Added DOI {doi} to existing article: {science_paper.title}",
					level=2,
				)

			# Check what needs to be updated
			basic_fields_changed = self.article_needs_update(
				science_paper, title, summary, link, published_date
			)
			crossref_fields_changed = self.crossref_needs_update(
				science_paper, container_title, publisher, access, crossref_check, pdf_link
			)

			# Update fields if anything has changed
			if basic_fields_changed or crossref_fields_changed:
				self.update_all_article_fields(
					science_paper,
					title,
					summary,
					link,
					published_date,
					container_title,
					publisher,
					access,
					crossref_check,
					pdf_link,
				)
				crossref_was_updated = crossref_fields_changed
		else:
			# Create new article
			article_data = {
				"title": title,
				"summary": summary,
				"link": link,
				"links": merge_links(None, link),
				"published_date": published_date,
				"crossref_check": crossref_check,
			}
			if doi:
				article_data.update(
					{
						"doi": doi,
						"container_title": container_title,
						"publisher": publisher,
						"access": access,
						"pdf_link": pdf_link,
					}
				)

			science_paper = Articles.objects.create(**article_data)
			created = True
			# For new articles with CrossRef data, authors should be processed
			crossref_was_updated = crossref_check is not None

		# Add relationships
		self.add_article_relationships(science_paper, source)

		return science_paper, created, crossref_was_updated

	def article_needs_update(
		self, article: Articles, title: str, summary: str, link: str, published_date
	) -> bool:
		"""Check if article needs to be updated."""
		return any(
			[
				article.title != title,
				article.summary != summary,
				merge_links(article.links, link) != (article.links or {}),
				# A date-less feed entry must never blank an existing date
				published_date is not None
				and article.published_date != published_date,
			]
		)

	def update_article_fields(
		self, article: Articles, title: str, summary: str, link: str, published_date
	):
		"""Update article fields."""
		article.title = title
		article.summary = summary
		# Never overwrite the first-seen link; merge the incoming URL into links instead
		merged = merge_links(article.links, link)
		if merged != (article.links or {}):
			article.links = merged
		if published_date is not None:
			article.published_date = published_date
		article.save()

	def update_all_article_fields(
		self,
		article: Articles,
		title: str,
		summary: str,
		link: str,
		published_date,
		container_title: str,
		publisher: str,
		access: str,
		crossref_check,
		pdf_link=None,
	):
		"""Update all article fields (basic + CrossRef) in a single operation."""
		article.title = title
		article.summary = summary
		# Never overwrite the first-seen link; merge the incoming URL into links instead
		merged = merge_links(article.links, link)
		if merged != (article.links or {}):
			article.links = merged
		if published_date is not None:
			article.published_date = published_date
		# Only touch CrossRef-derived fields when this update actually carries
		# CrossRef data; a feed-only update (crossref_check=None) must never
		# blank container_title/publisher/access gathered by an earlier run.
		if crossref_check is not None:
			article.container_title = container_title
			article.publisher = publisher
			article.access = access
			article.crossref_check = crossref_check
			if pdf_link:
				article.pdf_link = pdf_link
		article.save()
		self.log(f" Updated article data: {article.title}", level=2)

	def crossref_needs_update(
		self,
		article: Articles,
		container_title: str,
		publisher: str,
		access: str,
		crossref_check,
		pdf_link=None,
	) -> bool:
		"""Check if CrossRef data needs to be updated."""
		# If we have new CrossRef data and the article doesn't have it
		if crossref_check and not article.crossref_check:
			return True

		# If CrossRef data has changed
		if crossref_check and any(
			[
				article.container_title != container_title,
				article.publisher != publisher,
				article.access != access,
				pdf_link and article.pdf_link != pdf_link,
			]
		):
			return True

		return False

	def update_crossref_fields(
		self,
		article: Articles,
		container_title: str,
		publisher: str,
		access: str,
		crossref_check,
	):
		"""Update CrossRef-related fields."""
		article.container_title = container_title
		article.publisher = publisher
		article.access = access
		article.crossref_check = crossref_check
		article.save()
		self.log(f" Updated CrossRef data for article: {article.title}", level=2)

	def add_article_relationships(self, article: Articles, source: Sources):
		"""Add relationships between article and source (team, subject, sources)."""
		if source.team:
			article.teams.add(source.team)
		else:
			self.log(
				f"Warning: Source '{source.name}' has no team assigned. Skipping team association.",
				level=1,
				style_func=self.style.WARNING,
			)
		if source.subject:
			article.subjects.add(source.subject)
		article.sources.add(source)
		if hasattr(article, "save"):
			article.save()

	def process_authors(self, crossref_paper: SciencePaper, science_paper: Articles):
		"""Process author information from CrossRef data."""
		if crossref_paper is None or crossref_paper.authors is None:
			return

		for author_info in crossref_paper.authors:
			given_name = author_info.get("given")
			family_name = author_info.get("family")
			orcid = (
				normalize_orcid(author_info.get("ORCID"))
				if author_info.get("ORCID")
				else None
			)

			try:
				author_obj = self.get_or_create_author(given_name, family_name, orcid)
				if (
					author_obj
					and not science_paper.authors.filter(pk=author_obj.pk).exists()
				):
					science_paper.authors.add(author_obj)
			except Exception as e:
				self.log(
					f"Error processing author {given_name} {family_name}: {str(e)}",
					level=2,
				)
				continue

	def get_or_create_author(
		self, given_name: str, family_name: str, orcid: str
	) -> Authors:
		"""Get or create an author object."""
		try:
			if orcid:  # If ORCID is present, use it as primary key
				author_obj, author_created = Authors.objects.get_or_create(
					ORCID=orcid,
					defaults={
						"given_name": given_name or "",
						"family_name": family_name or "",
					},
				)
			else:  # If no ORCID, use given_name and family_name
				if not given_name or not family_name:
					return None

				author_obj, author_created = Authors.objects.get_or_create(
					given_name=given_name,
					family_name=family_name,
					defaults={"ORCID": orcid},
				)

			return author_obj

		except MultipleObjectsReturned:
			# Handle multiple authors case
			authors = Authors.objects.filter(
				given_name=given_name, family_name=family_name
			)
			# Use the first author with an ORCID, if available
			return next((author for author in authors if author.ORCID), authors.first())
