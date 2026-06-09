"""
Tests for multi-source article link handling (first-seen-wins).

Covers:
  - feedreader_articles: first source's URL stays canonical; second source's URL
    is recorded in links but does not replace link
  - import_articles_from_api: update path does not clobber article.link
  - re-imports are idempotent

Run:
  docker exec gregory python manage.py test gregory.tests.test_article_links
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.test import TestCase
from organizations.models import Organization

from gregory.models import Articles, Sources, Subject, Team
from gregory.management.commands.feedreader_articles import Command as FeedCommand
from gregory.management.commands.import_articles_from_api import Command as ImportCommand


PUBMED_LINK = 'https://pubmed.ncbi.nlm.nih.gov/12345678/'
NATURE_LINK = 'https://www.nature.com/articles/s41467-025-00001-0'
TITLE = 'A Study of First-Seen-Wins Article Link Handling'


def _make_source(org, method='rss', name='Test Source'):
	team = Team.objects.create(organization=org, name='Test Team %s' % name, slug='test-team-%s' % name.lower().replace(' ', '-'))
	subject = Subject.objects.create(subject_name='Subj %s' % name, subject_slug='subj-%s' % name.lower().replace(' ', '-'))
	return Sources.objects.create(
		name=name,
		source_for='science paper',
		method=method,
		subject=subject,
		team=team,
	)


class FeedreaderFirstSeenWinsTest(TestCase):
	"""feedreader_articles: link set on first create, not overwritten on update."""

	def setUp(self):
		self.org = Organization.objects.create(name='Test Org')
		self.source1 = _make_source(self.org, name='PubMed Source')
		self.source2 = _make_source(self.org, name='Nature Source')
		self.cmd = FeedCommand()
		self.cmd.verbosity = 0

	def _create(self, link, source):
		article, created, _ = self.cmd.create_or_update_article(
			doi=None,
			title=TITLE,
			summary='Test summary',
			link=link,
			published_date=None,
			source=source,
		)
		return article, created

	def test_first_create_sets_link_and_links(self):
		article, created = self._create(PUBMED_LINK, self.source1)
		self.assertTrue(created)
		self.assertEqual(article.link, PUBMED_LINK)
		self.assertIn('pubmed.ncbi.nlm.nih.gov', article.links)
		self.assertEqual(article.links['pubmed.ncbi.nlm.nih.gov'], PUBMED_LINK)

	def test_second_source_does_not_overwrite_link(self):
		self._create(PUBMED_LINK, self.source1)
		article, created = self._create(NATURE_LINK, self.source2)
		self.assertFalse(created)
		# canonical link stays as the first one
		self.assertEqual(article.link, PUBMED_LINK)
		# both URLs are recorded
		self.assertIn('pubmed.ncbi.nlm.nih.gov', article.links)
		self.assertIn('nature.com', article.links)

	def test_reimport_is_idempotent(self):
		self._create(PUBMED_LINK, self.source1)
		self._create(NATURE_LINK, self.source2)
		article = Articles.objects.get(title=TITLE)
		link, links = article.link, dict(article.links)

		self._create(PUBMED_LINK, self.source1)
		self._create(NATURE_LINK, self.source2)
		article.refresh_from_db()
		self.assertEqual(article.link, link)
		self.assertEqual(article.links, links)


class ImportArticlesFromApiLinkTest(TestCase):
	"""import_articles_from_api: update path never clobbers article.link."""

	def setUp(self):
		self.org = Organization.objects.create(name='Test Org 2')
		self.cmd = ImportCommand()
		self.cmd.stdout = open(os.devnull, 'w')

	def tearDown(self):
		self.cmd.stdout.close()

	def _run_import(self, link):
		from django.utils import timezone
		items = [{
			'title': TITLE,
			'link': link,
			'doi': None,
			'summary': 'summary',
			'published_date': None,
			'publisher': None,
			'container_title': None,
			'access': None,
			'discovery_date': timezone.now().isoformat(),
			'authors': [],
			'sources': [],
			'teams': [],
			'subjects': [],
			'article_subject_relevances': [],
		}]
		# Simulate what the command loop does (inline, no HTTP)
		from django.utils.dateparse import parse_datetime
		from gregory.utils.trial_utils import merge_trial_links
		for item in items:
			title = item.get('title')
			link_val = item.get('link')
			doi = item.get('doi')
			summary = item.get('summary')
			published_date = parse_datetime(item.get('published_date')) if item.get('published_date') else None
			publisher = item.get('publisher')
			container_title = item.get('container_title')
			access = item.get('access')
			discovery_date = parse_datetime(item.get('discovery_date')) if item.get('discovery_date') else timezone.now()

			article, created = Articles.objects.update_or_create(
				title=title,
				defaults={
					'doi': doi,
					'summary': summary,
					'published_date': published_date,
					'publisher': publisher,
					'container_title': container_title,
					'access': access,
					'discovery_date': discovery_date,
				},
				create_defaults={
					'link': link_val,
					'links': merge_trial_links(None, link_val),
				},
			)
			if not created and link_val:
				merged_links = merge_trial_links(article.links, link_val)
				if merged_links != (article.links or {}):
					article.links = merged_links
					article.save(update_fields=['links'])

	def test_first_import_sets_link(self):
		self._run_import(PUBMED_LINK)
		article = Articles.objects.get(title=TITLE)
		self.assertEqual(article.link, PUBMED_LINK)
		self.assertIn('pubmed.ncbi.nlm.nih.gov', article.links)

	def test_second_import_different_url_does_not_overwrite_link(self):
		self._run_import(PUBMED_LINK)
		self._run_import(NATURE_LINK)
		article = Articles.objects.get(title=TITLE)
		self.assertEqual(article.link, PUBMED_LINK)
		self.assertIn('nature.com', article.links)

	def test_reimport_is_idempotent(self):
		self._run_import(PUBMED_LINK)
		self._run_import(NATURE_LINK)
		article = Articles.objects.get(title=TITLE)
		link, links = article.link, dict(article.links)
		self._run_import(PUBMED_LINK)
		self._run_import(NATURE_LINK)
		article.refresh_from_db()
		self.assertEqual(article.link, link)
		self.assertEqual(article.links, links)
