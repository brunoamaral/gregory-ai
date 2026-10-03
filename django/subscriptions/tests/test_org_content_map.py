"""
Regression guard: org_content_map must be populated (non-empty) for team-owned
emails when matching ArticleSiteContent rows exist.

Catches future re-introduction of the silent empty-map fallback in
templates/emails/components/content_organizer.py.
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")

import django

django.setup()

from django.contrib.sites.models import Site
from django.test import TestCase

from gregory.models import (
	Articles,
	ArticleSiteContent,
	OrganizationSite,
	Subject,
	Team,
)
from organizations.models import Organization
from templates.emails.components.content_organizer import EmailRenderingPipeline


class OrgContentMapTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Map Org", slug="map-org")
		self.team = Team.objects.create(
			name="Map Team",
			organization=self.org,
			slug="map-team",
		)
		self.subject = Subject.objects.create(
			subject_name="Map Subject",
			team=self.team,
			subject_slug="map-subject",
		)
		self.site = Site.objects.create(domain="maporg.example.com", name="Map Org")
		OrganizationSite.objects.create(
			organization=self.org, site=self.site, is_default=True
		)
		self.article = Articles.objects.create(
			title="Test article",
			link="https://example.com/article/1",
		)
		self.article.teams.add(self.team)
		ArticleSiteContent.objects.create(
			article=self.article,
			site=self.site,
			takeaways="Key finding",
		)

	def test_org_content_map_populated_for_team_email(self):
		pipeline = EmailRenderingPipeline()
		context = pipeline.prepare_optimized_context(
			email_type="weekly_summary",
			articles=Articles.objects.filter(pk=self.article.pk),
			organization=self.org,
			site=self.site,
		)
		self.assertIn(self.article.article_id, context["org_content_map"])
		oc = context["org_content_map"][self.article.article_id]
		self.assertEqual(oc.takeaways, "Key finding")

	def _takeaways_for(self, site):
		context = EmailRenderingPipeline().prepare_optimized_context(
			email_type="weekly_summary",
			articles=Articles.objects.filter(pk=self.article.pk),
			organization=self.org,
			site=site,
		)
		content = context["org_content_map"].get(self.article.article_id)
		return content.takeaways if content else None

	def test_org_content_map_uses_the_site_the_email_is_sent_for(self):
		"""Editorial content is per site; an email sent for one of the
		organisation's sites carries that site's text, not the default's."""
		other = Site.objects.create(domain="maporg-two.example.com", name="Map Two")
		OrganizationSite.objects.create(organization=self.org, site=other)
		ArticleSiteContent.objects.create(
			article=self.article, site=other, takeaways="Other site text"
		)

		self.assertEqual(self._takeaways_for(other), "Other site text")
		self.assertEqual(self._takeaways_for(self.site), "Key finding")

	def test_org_content_map_falls_back_to_the_default_site(self):
		"""With no site, or one the organisation doesn't own, the email carries
		the organisation's default site text and never the foreign site's."""
		foreign_org = Organization.objects.create(name="Foreign", slug="foreign")
		foreign = Site.objects.create(domain="foreign.example.com", name="Foreign")
		OrganizationSite.objects.create(organization=foreign_org, site=foreign)
		ArticleSiteContent.objects.create(
			article=self.article, site=foreign, takeaways="Foreign text"
		)

		self.assertEqual(self._takeaways_for(None), "Key finding")
		self.assertEqual(self._takeaways_for(foreign), "Key finding")

	def test_org_content_map_empty_and_warns_for_team_type_without_organization(self):
		"""
		For email types that expect an org (weekly_summary, admin_summary),
		omitting organization= must produce an empty map AND emit a WARNING so
		the caller can be identified and fixed.
		"""
		pipeline = EmailRenderingPipeline()
		logger_name = "templates.emails.components.content_organizer"
		with self.assertLogs(logger_name, level="WARNING") as cm:
			context = pipeline.prepare_optimized_context(
				email_type="weekly_summary",
				articles=Articles.objects.none(),
				organization=None,
				site=self.site,
			)
		self.assertEqual(context["org_content_map"], {})
		self.assertTrue(
			any("org_content_map" in msg or "organization" in msg for msg in cm.output),
			msg=f"Expected org warning in logs, got: {cm.output}",
		)

	def test_org_content_map_empty_no_warning_for_non_org_type(self):
		"""Email types outside _ORG_EXPECTED_TYPES omit organization= without a
		warning. trial_notification used to be one of these, but now expects
		organization= like weekly_summary/admin_summary do — see
		test_trial_notification_organization.py."""
		pipeline = EmailRenderingPipeline()
		logger_name = "templates.emails.components.content_organizer"
		import logging

		with self.assertLogs(logger_name, level="DEBUG") as cm:
			# Emit a dummy DEBUG so assertLogs doesn't raise on empty log output
			logging.getLogger(logger_name).debug("sentinel")
			context = pipeline.prepare_optimized_context(
				email_type="test_components",
				articles=Articles.objects.none(),
				organization=None,
				site=self.site,
			)
		self.assertEqual(context["org_content_map"], {})
		self.assertFalse(
			any("WARNING" in msg and "organization" in msg for msg in cm.output),
			msg=f"Unexpected WARNING for test_components: {cm.output}",
		)
