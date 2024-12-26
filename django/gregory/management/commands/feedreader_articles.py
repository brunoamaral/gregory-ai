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
							self.stdout.write(f"Processing {title}")
							summary = entry.get('summary', '')
							if hasattr(entry, 'summary_detail'):
									summary = entry['summary_detail']['value']
							published = entry.get('published')
							if 'pubmed' in source.link and hasattr(entry, 'content'):
									summary = entry['content'][0]['value']
							published_date = parse(entry.get('published') or entry.get('prism_coverdate'), tzinfos=self.tzinfos).astimezone(pytz.utc)
							link = greg.remove_utm(entry['link'])
							doi = None
							if 'pubmed' in source.link and entry.get('dc_identifier', '').startswith('doi:'):
									doi = entry['dc_identifier'].replace('doi:', '')
							elif 'faseb' in source.link:
									doi = entry.get('prism_doi', '')

							if doi:
									crossref_paper = SciencePaper(doi=doi)
									crossref_paper.refresh()
									title = crossref_paper.title if crossref_paper.title else entry['title']
									summary = crossref_paper.abstract if crossref_paper.abstract else entry.get('summary')

									# Check if an article with the same DOI or title exists
									existing_article = Articles.objects.filter(Q(doi=doi) | Q(title=title)).first()
									if existing_article:
										science_paper = existing_article
										created = False
									else:
										update_change_reason(existing_article, "Creating new article from RSS feed")
										science_paper = Articles.objects.create(
											doi=doi,
											title=title,
											summary=summary,
											link=link,
											published_date=published_date,
											container_title=crossref_paper.journal,
											publisher=crossref_paper.publisher,
											access=crossref_paper.access,
											crossref_check=timezone.now()
										)
										created = True

									if created:
										update_change_reason(science_paper, "Assigning teams, subjects, and sources")
										science_paper.teams.add(source.team)
										science_paper.subjects.add(source.subject)
										science_paper.sources.add(source)
										science_paper.save()
									else:
											if any([science_paper.title != title, science_paper.summary != SciencePaper.clean_abstract(abstract=summary),science_paper.link != link, science_paper.published_date != published_date,]):
													update_change_reason(science_paper, "Updating article with new data from RSS feed")
													science_paper.title = title
													science_paper.summary = SciencePaper.clean_abstract(abstract=summary)
													science_paper.link = link
													science_paper.published_date = published_date
													science_paper.sources.add(source)
													science_paper.teams.add(source.team)
													science_paper.subjects.add(source.subject)
													science_paper.save()

									# Process author information
									if crossref_paper and crossref_paper.authors:
											for author_info in crossref_paper.authors:
												given_name = author_info.get('given')
												family_name = author_info.get('family')
												orcid = author_info.get('ORCID', None)
												try:
													if orcid:  # If ORCID is present, use it as the primary key for author lookup/creation
														update_change_reason(existing_article, "Adding or retrieving author by ORCID")
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
														update_change_reason(existing_article, "Adding or retrieving author by name")
														author_obj, author_created = Authors.objects.get_or_create(
																given_name=given_name,
																family_name=family_name,
																defaults={'ORCID': orcid}  # orcid will be an empty string if not provided, which is fine
															)
												except MultipleObjectsReturned:
													# Handle the case where multiple authors are returned
													authors = Authors.objects.filter(given_name=given_name, family_name=family_name)
													print(f"Multiple authors found for {given_name} {family_name}:")
													for author in authors:
															print(f"Author ID: {author.author_id}, ORCID: {author.ORCID}")
													# Use the first author with an ORCID, if available
													author_obj = next((author for author in authors if author.ORCID), authors.first())

													# Link the author to the article if not already linked
												if not science_paper.authors.filter(pk=author_obj.pk).exists():
													update_change_reason(science_paper, "Linking author to article")
													science_paper.authors.add(author_obj)
							else:
								print('No DOI, trying to create article')
								existing_article = Articles.objects.filter(title=title).first()
								if existing_article:
											science_paper = existing_article
											created = False
								else:
											update_change_reason(existing_article, "Creating new article without DOI from RSS feed")
											science_paper = Articles.objects.create(
												title=title,
												summary=summary,
												link=link,
												published_date=published_date,
												crossref_check=None
											)
											update_change_reason(science_paper, "Assigning teams, sources, and subjects")
											science_paper.teams.add(source.team)
											science_paper.sources.add(source)
											science_paper.subjects.add(source.subject)
											created = True

								if not created:
									if any([science_paper.title != title, science_paper.summary != SciencePaper.clean_abstract(abstract=summary),
												science_paper.link != link, science_paper.published_date != published_date]):
										update_change_reason(science_paper, "Updating existing article without DOI")
										science_paper.title = title
										science_paper.summary = SciencePaper.clean_abstract(abstract=summary)
										science_paper.link = link
										science_paper.published_date = published_date
										science_paper.teams.add(source.team)
										science_paper.subjects.add(source.subject)
										science_paper.sources.add(source)
										science_paper.save()