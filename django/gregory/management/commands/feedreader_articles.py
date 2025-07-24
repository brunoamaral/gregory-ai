from django.core.management.base import BaseCommand
from gregory.models import Articles, Trials, Sources, Authors
from crossref.restful import Works, Etiquette
from dateutil.parser import parse
from dateutil.tz import gettz
from django.core.exceptions import MultipleObjectsReturned
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone
from gregory.classes import SciencePaper
from sitesettings.models import CustomSetting
import feedparser
import gregory.functions as greg
import os
import pytz
import re
import requests
from simple_history.utils import update_change_reason
from abc import ABC, abstractmethod


class FeedProcessor(ABC):
    """Abstract base class for RSS feed processors."""
    
    def __init__(self, command_instance):
        self.command = command_instance
    
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
            entry.get('published') or 
            entry.get('prism_coverdate') or 
            entry.get('updated') or 
            entry.get('date') or 
            entry.get('prism_publicationdate')
        )
        
        if not date_string:
            raise ValueError(f"No valid date field found in feed entry: {entry.get('title', 'Unknown')}")
        
        published_date = parse(date_string, tzinfos=self.command.tzinfos).astimezone(pytz.utc)
        
        return {
            'title': entry['title'],
            'link': greg.remove_utm(entry['link']),
            'published_date': published_date,
        }
        
    def should_include_article(self, entry: dict, source: 'Sources') -> bool:
        """Check if article should be included based on keyword filtering."""
        if not source.keyword_filter:
            return True  # No filter, include all articles
        
        # Get text content to search
        title = entry.get('title', '').lower()
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
        parts = [part.strip() for part in keyword_filter.split(',')]
        
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
        return 'pubmed' in source_link.lower()
    
    def extract_summary(self, entry: dict) -> str:
        """Extract summary with PubMed-specific priority."""
        summary = entry.get('summary', '')
        if hasattr(entry, 'summary_detail'):
            summary = entry['summary_detail']['value']
        if hasattr(entry, 'content'):
            summary = entry['content'][0]['value']
        return summary
    
    def extract_doi(self, entry: dict) -> str:
        """Extract DOI from PubMed feed entry."""
        if entry.get('dc_identifier', '').startswith('doi:'):
            return entry['dc_identifier'].replace('doi:', '')
        return None


class FasebFeedProcessor(FeedProcessor):
    """Processor for FASEB RSS feeds."""
    
    def can_process(self, source_link: str) -> bool:
        return 'faseb' in source_link.lower()
    
    def extract_summary(self, entry: dict) -> str:
        """Extract summary for FASEB feeds."""
        summary = entry.get('summary', '')
        if hasattr(entry, 'summary_detail'):
            summary = entry['summary_detail']['value']
        return summary
    
    def extract_doi(self, entry: dict) -> str:
        """Extract DOI from FASEB feed entry."""
        return entry.get('prism_doi', '')


class BioRxivFeedProcessor(FeedProcessor):
    """Processor for bioRxiv and medRxiv RSS feeds with keyword filtering support."""
    
    def can_process(self, source_link: str) -> bool:
        link = source_link.lower()
        return 'biorxiv' in link or 'medrxiv' in link
    
    def extract_summary(self, entry: dict) -> str:
        """Extract summary for bioRxiv and medRxiv feeds."""
        # Prefer description field for bioRxiv/medRxiv
        summary = entry.get('description', '')
        if not summary and hasattr(entry, 'summary_detail'):
            summary = entry['summary_detail'].get('value', '') or ''
        elif not summary:
            summary = entry.get('summary', '')
        return summary or ''
    
    def extract_doi(self, entry: dict) -> str:
        """Extract DOI from bioRxiv or medRxiv feed entry."""
        # bioRxiv and medRxiv use dc:identifier with doi: prefix
        dc_identifier = entry.get('dc_identifier', '')
        if dc_identifier.startswith('doi:'):
            return dc_identifier.replace('doi:', '')
        return None


class NatureFeedProcessor(FeedProcessor):
    """Processor for Nature.com RSS feeds."""
    
    def can_process(self, source_link: str) -> bool:
        return 'nature.com' in source_link.lower()
    
    def extract_summary(self, entry: dict) -> str:
        """Extract summary for Nature feeds - usually empty in Nature RSS."""
        summary = entry.get('summary', '')
        if hasattr(entry, 'summary_detail'):
            summary = entry['summary_detail']['value']
        return summary
    
    def extract_doi(self, entry: dict) -> str:
        """Extract DOI from Nature feed entry link."""
        link = entry.get('link', '')
        if not link:
            return None
        
        # Nature links are in format: https://www.nature.com/articles/s41467-025-61751-9
        # Extract the partial DOI from the link and construct full DOI
        if '/articles/' in link:
            try:
                partial_doi = link.split('/articles/')[-1]
                # Remove any query parameters or fragments
                partial_doi = partial_doi.split('?')[0].split('#')[0]
                # Construct full DOI with Nature prefix
                if partial_doi:
                    return f"10.1038/{partial_doi}"
            except Exception:
                pass
        
        return None


class DefaultFeedProcessor(FeedProcessor):
    """Default processor for generic RSS feeds."""
    
    def can_process(self, source_link: str) -> bool:
        return True  # Always can process as fallback
    
    def extract_summary(self, entry: dict) -> str:
        """Extract summary for generic feeds."""
        summary = entry.get('summary', '')
        if hasattr(entry, 'summary_detail'):
            summary = entry['summary_detail']['value']
        return summary
    
    def extract_doi(self, entry: dict) -> str:
        """Generic DOI extraction - returns None as most feeds don't have DOI."""
        return None


class PNASFeedProcessor(FeedProcessor):
    """Processor for PNAS RSS 1.0/RDF format feeds."""
    
    def can_process(self, source_link: str) -> bool:
        return 'pnas.org' in source_link.lower()
    
    def extract_summary(self, entry: dict) -> str:
        """Extract summary from PNAS feed entry."""
        summary = entry.get('description', '')
        
        # PNAS descriptions typically have the format:
        # "Proceedings of the National Academy of Sciences, Volume X, Issue Y, Month Year. <br/>SignificanceText..."
        if summary and '<br/>' in summary:
            # Extract only the content after the <br/> tag which contains the actual abstract
            parts = summary.split('<br/>')
            if len(parts) > 1 and parts[1].strip():
                return parts[1].strip()
        
        return summary
    
    def extract_doi(self, entry: dict) -> str:
        """Extract DOI from PNAS feed entry."""
        # PNAS provides DOI in dc:identifier field with 'doi:' prefix
        dc_identifier = entry.get('dc_identifier', '')
        if dc_identifier.startswith('doi:'):
            return dc_identifier.replace('doi:', '')
        
        # Alternatively, check prism:doi if available
        prism_doi = entry.get('prism_doi', '')
        if prism_doi:
            return prism_doi
            
        return None


class Command(BaseCommand):
    help = 'Fetches and updates articles and trials from RSS feeds.'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.feed_processors = [
            PubMedFeedProcessor(self),
            FasebFeedProcessor(self),
            BioRxivFeedProcessor(self),
            PNASFeedProcessor(self),
            NatureFeedProcessor(self),
            DefaultFeedProcessor(self),  # Always last as fallback
        ]
        self.verbosity = 1  # Default verbosity level

    def handle(self, *args, **options):
        self.verbosity = options.get('verbosity', 1)
        self.setup()
        self.update_articles_from_feeds()
        
    def log(self, message, level=2, style_func=None):
        """
        Log a message if the verbosity level is high enough.
        
        Levels:
        0 = Silent
        1 = Only main processing steps (feeds, sources)
        2 = Detailed information (default for most messages)
        3 = Debug information
        """
        if self.verbosity >= level:
            if style_func:
                self.stdout.write(style_func(message))
            else:
                self.stdout.write(message)

    def setup(self):
        self.SITE = CustomSetting.objects.get(site__domain=os.environ.get('DOMAIN_NAME'))
        self.CLIENT_WEBSITE = f'https://{self.SITE.site.domain}/'
        my_etiquette = Etiquette(self.SITE.title, 'v8', self.CLIENT_WEBSITE, self.SITE.admin_email)
        self.works = Works(etiquette=my_etiquette)
        self.tzinfos = {"EDT": gettz("America/New_York"), "EST": gettz("America/New_York")}

    def fetch_feed(self, link, ignore_ssl):
        if not ignore_ssl:
            return feedparser.parse(link)
        else:
            response = requests.get(link, verify=False)
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
        sources = Sources.objects.filter(method='rss', source_for='science paper', active=True)
        for source in sources:
            self.log(f'# Processing articles from {source}', level=1)
            feed = self.fetch_feed(source.link, source.ignore_ssl)
            processor = self.get_feed_processor(source.link)
            
            for entry in feed['entries']:
                try:
                    # Check if the article should be included based on keyword filtering
                    if hasattr(processor, 'should_include_article') and not processor.should_include_article(entry, source):
                        self.log(f"  ➡️  Excluded by keyword filter: {entry.get('title', 'Unknown')}", level=2)
                        continue
                    
                    self.process_feed_entry(entry, source, processor)
                except Exception as e:
                    self.log(f"Error processing entry '{entry.get('title', 'Unknown')}': {str(e)}", level=2)
                    continue

    def process_feed_entry(self, entry: dict, source: Sources, processor: FeedProcessor):
        """Process a single feed entry."""
        # Extract basic fields common to all feed types
        basic_fields = processor.extract_basic_fields(entry)
        title = basic_fields['title']
        link = basic_fields['link']
        published_date = basic_fields['published_date']
        
        # Extract feed-specific fields
        raw_summary = processor.extract_summary(entry)
        doi = processor.extract_doi(entry)
        
        # Clean and store the original feed summary
        feed_summary = SciencePaper.clean_abstract(abstract=raw_summary) if raw_summary else ''
        
        if doi:
            self.process_article_with_doi(
                doi, title, feed_summary, link, published_date, source
            )
        else:
            self.process_article_without_doi(
                title, feed_summary, link, published_date, source
            )

    def process_article_with_doi(self, doi: str, title: str, feed_summary: str, 
                                link: str, published_date, source: Sources):
        """Process an article that has a DOI."""
        crossref_paper = SciencePaper(doi=doi)
        refresh_result = crossref_paper.refresh()
        
        # Determine article data based on CrossRef success/failure
        article_data = self.get_article_data_with_crossref(
            crossref_paper, refresh_result, title, feed_summary, doi
        )
        
        # Create or update article
        science_paper, created, crossref_was_updated = self.create_or_update_article(
            doi=doi, title=article_data['title'], summary=article_data['summary'],
            link=link, published_date=published_date, source=source,
            container_title=article_data['container_title'],
            publisher=article_data['publisher'],
            access=article_data['access'],
            crossref_check=article_data['crossref_check']
        )
        
        # Process authors if:
        # 1. CrossRef data is available AND
        # 2. (Article was created OR CrossRef data was updated for existing article)
        if self.is_crossref_successful(refresh_result) and (created or crossref_was_updated):
            self.log(f" Processing authors for {'new' if created else 'updated'} article: {science_paper.title}", level=2)
            self.process_authors(crossref_paper, science_paper)

    def process_article_without_doi(self, title: str, feed_summary: str, 
                                   link: str, published_date, source: Sources):
        """Process an article that doesn't have a DOI."""
        self.log('No DOI, trying to create article', level=2)
        
        # Log potential summary truncation issues
        if 20 < len(feed_summary) < 500:
            self.log(f"Warning: Potentially truncated summary for title '{title}': {len(feed_summary)} characters", level=2)
        
        # Create or update article (no CrossRef data, so crossref_was_updated will be False)
        science_paper, created, crossref_was_updated = self.create_or_update_article(
            doi=None, title=title, summary=feed_summary, 
            link=link, published_date=published_date, source=source,
            crossref_check=None
        )

    def get_article_data_with_crossref(self, crossref_paper: SciencePaper, refresh_result,
                                      title: str, feed_summary: str, doi: str) -> dict:
        """Get article data based on CrossRef lookup results."""
        if self.is_crossref_failed(refresh_result):
            self.log(f"  ⚠️  CrossRef lookup failed for DOI {doi}: {refresh_result}", level=2)
            return {
                'title': title,
                'summary': feed_summary,
                'container_title': None,
                'publisher': None,
                'access': None,
                'crossref_check': None
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
                self.log(f"  ⚠️  Potentially truncated summary for DOI {doi}: {len(summary)} characters", level=2)
            
            return {
                'title': crossref_title,
                'summary': summary,
                'container_title': crossref_paper.journal,
                'publisher': crossref_paper.publisher,
                'access': crossref_paper.access,
                'crossref_check': timezone.now()
            }

    def is_crossref_failed(self, refresh_result) -> bool:
        """Check if CrossRef refresh failed."""
        return (isinstance(refresh_result, str) and 
                any(keyword in refresh_result.lower() 
                    for keyword in ['error', 'not found', 'json decode']))

    def is_crossref_successful(self, refresh_result) -> bool:
        """Check if CrossRef refresh was successful."""
        return not self.is_crossref_failed(refresh_result)

    def create_or_update_article(self, doi: str, title: str, summary: str, link: str,
                                published_date, source: Sources, container_title=None,
                                publisher=None, access=None, crossref_check=None) -> tuple[Articles, bool, bool]:
        """Create a new article or update existing one. Returns (article, created, crossref_updated)."""
        # Check if an article with the same DOI or title exists
        if doi:
            existing_article = Articles.objects.filter(Q(doi=doi) | Q(title=title)).first()
        else:
            existing_article = Articles.objects.filter(title=title).first()
        
        crossref_was_updated = False
        
        if existing_article:
            science_paper = existing_article
            created = False
            
            # Check what needs to be updated
            basic_fields_changed = self.article_needs_update(science_paper, title, summary, link, published_date)
            crossref_fields_changed = self.crossref_needs_update(science_paper, container_title, publisher, access, crossref_check)
            
            # Update fields if anything has changed
            if basic_fields_changed or crossref_fields_changed:
                self.update_all_article_fields(
                    science_paper, title, summary, link, published_date,
                    container_title, publisher, access, crossref_check
                )
                crossref_was_updated = crossref_fields_changed
        else:
            # Create new article
            article_data = {
                'title': title,
                'summary': summary,
                'link': link,
                'published_date': published_date,
                'crossref_check': crossref_check
            }
            if doi:
                article_data.update({
                    'doi': doi,
                    'container_title': container_title,
                    'publisher': publisher,
                    'access': access,
                })
            
            science_paper = Articles.objects.create(**article_data)
            created = True
            # For new articles with CrossRef data, authors should be processed
            crossref_was_updated = crossref_check is not None
        
        # Add relationships
        self.add_article_relationships(science_paper, source)
        
        return science_paper, created, crossref_was_updated

    def article_needs_update(self, article: Articles, title: str, summary: str, 
                            link: str, published_date) -> bool:
        """Check if article needs to be updated."""
        return any([
            article.title != title,
            article.summary != summary,
            article.link != link,
            article.published_date != published_date
        ])

    def update_article_fields(self, article: Articles, title: str, summary: str,
                             link: str, published_date):
        """Update article fields."""
        article.title = title
        article.summary = summary
        article.link = link
        article.published_date = published_date
        article.save()

    def update_all_article_fields(self, article: Articles, title: str, summary: str,
                                 link: str, published_date, container_title: str,
                                 publisher: str, access: str, crossref_check):
        """Update all article fields (basic + CrossRef) in a single operation."""
        article.title = title
        article.summary = summary
        article.link = link
        article.published_date = published_date
        article.container_title = container_title
        article.publisher = publisher
        article.access = access
        article.crossref_check = crossref_check
        article.save()
        self.log(f" Updated article data: {article.title}", level=2)

    def crossref_needs_update(self, article: Articles, container_title: str, 
                             publisher: str, access: str, crossref_check) -> bool:
        """Check if CrossRef data needs to be updated."""
        # If we have new CrossRef data and the article doesn't have it
        if crossref_check and not article.crossref_check:
            return True
        
        # If CrossRef data has changed
        if crossref_check and any([
            article.container_title != container_title,
            article.publisher != publisher,
            article.access != access
        ]):
            return True
        
        return False

    def update_crossref_fields(self, article: Articles, container_title: str,
                              publisher: str, access: str, crossref_check):
        """Update CrossRef-related fields."""
        article.container_title = container_title
        article.publisher = publisher
        article.access = access
        article.crossref_check = crossref_check
        article.save()
        self.log(f" Updated CrossRef data for article: {article.title}", level=2)

    def add_article_relationships(self, article: Articles, source: Sources):
        """Add relationships between article and source (team, subject, sources)."""
        article.teams.add(source.team)
        article.subjects.add(source.subject)
        article.sources.add(source)
        if hasattr(article, 'save'):
            article.save()

    def process_authors(self, crossref_paper: SciencePaper, science_paper: Articles):
        """Process author information from CrossRef data."""
        if crossref_paper is None or crossref_paper.authors is None:
            return
        
        for author_info in crossref_paper.authors:
            given_name = author_info.get('given')
            family_name = author_info.get('family')
            orcid = author_info.get('ORCID', None)
            
            try:
                author_obj = self.get_or_create_author(given_name, family_name, orcid)
                if author_obj and not science_paper.authors.filter(pk=author_obj.pk).exists():
                    science_paper.authors.add(author_obj)
            except Exception as e:
                self.log(f"Error processing author {given_name} {family_name}: {str(e)}", level=2)
                continue

    def get_or_create_author(self, given_name: str, family_name: str, orcid: str) -> Authors:
        """Get or create an author object."""
        try:
            if orcid:  # If ORCID is present, use it as primary key
                author_obj, author_created = Authors.objects.get_or_create(
                    ORCID=orcid,
                    defaults={
                        'given_name': given_name or '',
                        'family_name': family_name or ''
                    }
                )
            else:  # If no ORCID, use given_name and family_name
                if not given_name or not family_name:
                    return None
                
                author_obj, author_created = Authors.objects.get_or_create(
                    given_name=given_name,
                    family_name=family_name,
                    defaults={'ORCID': orcid}
                )
            
            return author_obj
            
        except MultipleObjectsReturned:
            # Handle multiple authors case
            authors = Authors.objects.filter(given_name=given_name, family_name=family_name)
            # Use the first author with an ORCID, if available
            return next((author for author in authors if author.ORCID), authors.first())
