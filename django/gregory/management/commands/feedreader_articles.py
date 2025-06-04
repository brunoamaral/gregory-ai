from django.core.management.base import BaseCommand
from gregory.models import Articles, Trials, Sources, Authors
from crossref.restful import Works, Etiquette
from dateutil.parser import parse
from dateutil.tz import gettz
from django.core.exceptions import MultipleObjectsReturned
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone
from gregory.classes import SciencePaper, ClinicalTrial
from sitesettings.models import CustomSetting
import feedparser
import gregory.functions as greg
import os
import pytz
import re
import requests
from simple_history.utils import update_change_reason

class Command(BaseCommand):
		help = 'Fetches and updates articles and trials from RSS feeds.'

		def handle(self, *args, **options):
				self.setup()
				self.update_articles_from_feeds()

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
				print(f"An error occurred during {action}: {str(error)}")

		def update_articles_from_feeds(self):
			sources = Sources.objects.filter(method='rss', source_for='science paper', active=True)
			for source in sources:
					print(f'# Processing articles from {source}')
					feed = self.fetch_feed(source.link, source.ignore_ssl)
					for entry in feed['entries']:
							title = entry['title']
							
							# Extract summary with proper priority for PubMed feeds
							summary = entry.get('summary', '')
							if hasattr(entry, 'summary_detail'):
									summary = entry['summary_detail']['value']
							if 'pubmed' in source.link and hasattr(entry, 'content'):
									summary = entry['content'][0]['value']
							
							# Clean and store the original feed summary
							feed_summary = SciencePaper.clean_abstract(abstract=summary) if summary else ''
							
							published = entry.get('published')
							published_date = parse(entry.get('published') or entry.get('prism_coverdate'), tzinfos=self.tzinfos).astimezone(pytz.utc)
							link = greg.remove_utm(entry['link'])
							doi = None
							if 'pubmed' in source.link and entry.get('dc_identifier', '').startswith('doi:'):
									doi = entry['dc_identifier'].replace('doi:', '')
							elif 'faseb' in source.link:
									doi = entry.get('prism_doi', '')

							if doi:
									crossref_paper = SciencePaper(doi=doi)
									refresh_result = crossref_paper.refresh()
									
									# Check if CrossRef refresh was successful
									if isinstance(refresh_result, str) and any(keyword in refresh_result.lower() for keyword in ['error', 'not found', 'json decode']):
										print(f"  ⚠️  CrossRef lookup failed for DOI {doi}: {refresh_result}")
										# Use feed data as fallback
										title = entry['title']
										summary = feed_summary
										container_title = None
										publisher = None
										access = None
										crossref_check = None
									else:
										# CrossRef data available, use it with fallbacks
										title = crossref_paper.title if crossref_paper.title else entry['title']
										
										# Use crossref abstract if available, otherwise use the properly extracted feed summary
										if crossref_paper.abstract and crossref_paper.abstract.strip():
											summary = SciencePaper.clean_abstract(abstract=crossref_paper.abstract)
										else:
											summary = feed_summary
										
										container_title = crossref_paper.journal
										publisher = crossref_paper.publisher
										access = crossref_paper.access
										crossref_check = timezone.now()
									
									# Log potential summary truncation issues
									if 20 < len(summary) < 500:
										print(f"  ⚠️  Potentially truncated summary for DOI {doi}: {len(summary)} characters")

									# Check if an article with the same DOI or title exists
									existing_article = Articles.objects.filter(Q(doi=doi) | Q(title=title)).first()
									if existing_article:
										science_paper = existing_article
										created = False
									else:
										science_paper = Articles.objects.create(
											doi=doi,
											title=title,
											summary=summary,
											link=link,
											published_date=published_date,
											container_title=container_title,
											publisher=publisher,
											access=access,
											crossref_check=crossref_check
										)
										created = True

									if created:
										science_paper.teams.add(source.team)
										science_paper.subjects.add(source.subject)
										science_paper.sources.add(source)
										science_paper.save()
									else:
											if any([science_paper.title != title, science_paper.summary != summary,
													science_paper.link != link, science_paper.published_date != published_date]):
													science_paper.title = title
													science_paper.summary = summary
													science_paper.link = link
													science_paper.published_date = published_date
													science_paper.sources.add(source)
													science_paper.teams.add(source.team)
													science_paper.subjects.add(source.subject)
													science_paper.save()

									# Process author information only if CrossRef data was successfully retrieved
									if not isinstance(refresh_result, str) or 'not found' not in refresh_result.lower():
										if crossref_paper is not None and crossref_paper.authors is not None:
											for author_info in crossref_paper.authors:
												given_name = author_info.get('given')
												family_name = author_info.get('family')
												orcid = author_info.get('ORCID', None)
												try:
													if orcid:  # If ORCID is present, use it as the primary key for author lookup/creation
														author_obj, author_created = Authors.objects.get_or_create(
																ORCID=orcid,
																defaults={
																		'given_name': given_name or '',  # Empty string if missing
																		'family_name': family_name or ''  # Empty string if missing
																		}
																)
													else:  # If no ORCID is provided, fallback to using given_name and family_name for lookup/creation
														if not given_name or not family_name:
															self.stdout.write(f"Missing given name or family name, skipping this author. {crossref_paper.doi}")
															continue
														else:
															author_obj, author_created = Authors.objects.get_or_create(
																given_name=given_name,
																family_name=family_name,
																defaults={'ORCID': orcid}  # orcid will be an empty string if not provided, which is fine
															)
												except MultipleObjectsReturned:
													# Handle the case where multiple authors are returned
													authors = Authors.objects.filter(given_name=given_name, family_name=family_name)
													print(f"Multiple authors found for {given_name} {family_name}:")
													# Use the first author with an ORCID, if available
													author_obj = next((author for author in authors if author.ORCID), authors.first())

													# Link the author to the article if not already linked
												if not science_paper.authors.filter(pk=author_obj.pk).exists():
													science_paper.authors.add(author_obj)
							else:
								print('No DOI, trying to create article')
								
								# Use the properly extracted and cleaned feed summary
								summary = feed_summary
								
								# Log potential summary truncation issues
								if 20 < len(summary) < 500:
									self.stdout.write(f"Warning: Potentially truncated summary for title '{title}': {len(summary)} characters")
								
								existing_article = Articles.objects.filter(title=title).first()
								if existing_article:
											science_paper = existing_article
											created = False
								else:
											science_paper = Articles.objects.create(
												title=title,
												summary=summary,
												link=link,
												published_date=published_date,
												crossref_check=None
											)
											science_paper.teams.add(source.team)
											science_paper.sources.add(source)
											science_paper.subjects.add(source.subject)
											created = True

								if not created:
									if any([science_paper.title != title, science_paper.summary != summary,
												science_paper.link != link, science_paper.published_date != published_date]):
										science_paper.title = title
										science_paper.summary = summary
										science_paper.link = link
										science_paper.published_date = published_date
										science_paper.teams.add(source.team)
										science_paper.subjects.add(source.subject)
										science_paper.sources.add(source)
										science_paper.save()