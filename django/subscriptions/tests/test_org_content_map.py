"""
Regression guard: org_content_map must be populated (non-empty) for team-owned
emails when matching ArticleOrgContent rows exist.

Catches future re-introduction of the silent empty-map fallback in
templates/emails/components/content_organizer.py.
"""
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')

import django
django.setup()

from django.contrib.sites.models import Site
from django.test import TestCase

from gregory.models import Articles, ArticleOrgContent, Subject, Team
from organizations.models import Organization
from templates.emails.components.content_organizer import EmailRenderingPipeline


class OrgContentMapTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name='Map Org', slug='map-org')
		self.team = Team.objects.create(
			name='Map Team',
			organization=self.org,
			slug='map-team',
		)
		self.subject = Subject.objects.create(
			subject_name='Map Subject',
			team=self.team,
			subject_slug='map-subject',
		)
		self.site = Site.objects.create(domain='maporg.example.com', name='Map Org')
		self.article = Articles.objects.create(
			title='Test article',
			link='https://example.com/article/1',
		)
		self.article.teams.add(self.team)
		ArticleOrgContent.objects.create(
			article=self.article,
			organization=self.org,
			takeaways='Key finding',
		)

	def test_org_content_map_populated_for_team_email(self):
		pipeline = EmailRenderingPipeline()
		context = pipeline.prepare_optimized_context(
			email_type='weekly_summary',
			articles=Articles.objects.filter(pk=self.article.pk),
			organization=self.org,
			site=self.site,
		)
		self.assertIn(self.article.article_id, context['org_content_map'])
		oc = context['org_content_map'][self.article.article_id]
		self.assertEqual(oc.takeaways, 'Key finding')

	def test_org_content_map_empty_without_organization(self):
		"""Non-team path: organization=None should produce an empty map (with a warning)."""
		pipeline = EmailRenderingPipeline()
		context = pipeline.prepare_optimized_context(
			email_type='trial_notification',
			articles=Articles.objects.none(),
			organization=None,
			site=self.site,
		)
		self.assertEqual(context['org_content_map'], {})
