"""
Author names in the weekly digest and admin summary emails link to the
site's author profile page when CustomSetting.has_author_pages is on, and
to orcid.org otherwise. See AUTHOR-PAGES-SETTING-PLAN.md.

Run:
  docker exec gregory python manage.py test subscriptions.tests.test_author_page_links
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")

import django

django.setup()

from django.contrib.sites.models import Site
from django.template.loader import get_template
from django.test import TestCase

from gregory.models import Articles, Authors, Subject, Team
from organizations.models import Organization
from sitesettings.models import CustomSetting
from templates.emails.components.content_organizer import get_optimized_email_context


class AuthorPageLinkRenderingTest(TestCase):
	def setUp(self):
		self.organization = Organization.objects.create(
			name="Author Pages Org", slug="author-pages-org"
		)
		self.team = Team.objects.create(
			name="Author Pages Team",
			organization=self.organization,
			slug="author-pages-team",
		)
		self.subject = Subject.objects.create(
			subject_name="Author Pages Subject",
			team=self.team,
			subject_slug="author-pages-subject",
		)
		self.site = Site.objects.create(domain="brain-regeneration.com", name="BR")
		self.author = Authors.objects.create(
			given_name="Jane", family_name="Doe", ORCID="0000-0002-7922-9785"
		)
		self.article = Articles.objects.create(
			title="Author Pages Article",
			doi="10.9999/author-pages-1",
			link="https://example.com/author-pages-1",
		)
		self.article.subjects.add(self.subject)
		self.article.authors.add(self.author)

	def _render(self, template_name, email_type, has_author_pages):
		custom_settings = CustomSetting.objects.create(
			site=self.site,
			title=f"CS {email_type} {has_author_pages}",
			has_author_pages=has_author_pages,
		)
		context = get_optimized_email_context(
			email_type=email_type,
			articles=Articles.objects.filter(pk=self.article.pk),
			subscriber={"email": "admin@example.com"},
			site=self.site,
			custom_settings=custom_settings,
		)
		return get_template(template_name).render(context)

	def test_weekly_summary_links_to_site_when_flag_on(self):
		html = self._render(
			"emails/weekly_summary.html", "weekly_summary", has_author_pages=True
		)
		self.assertIn(
			'href="https://brain-regeneration.com/authors/0000-0002-7922-9785/"',
			html,
		)
		self.assertNotIn("orcid.org", html)

	def test_weekly_summary_links_to_orcid_when_flag_off(self):
		html = self._render(
			"emails/weekly_summary.html", "weekly_summary", has_author_pages=False
		)
		self.assertIn('href="https://orcid.org/0000-0002-7922-9785"', html)

	def test_admin_summary_links_to_site_when_flag_on(self):
		html = self._render(
			"emails/admin_summary.html", "admin_summary", has_author_pages=True
		)
		self.assertIn(
			'href="https://brain-regeneration.com/authors/0000-0002-7922-9785/"',
			html,
		)
		self.assertNotIn("orcid.org", html)

	def test_admin_summary_links_to_orcid_when_flag_off(self):
		html = self._render(
			"emails/admin_summary.html", "admin_summary", has_author_pages=False
		)
		self.assertIn('href="https://orcid.org/0000-0002-7922-9785"', html)
