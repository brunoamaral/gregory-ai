"""
End-to-end check that add_utm_params only tags links pointing at the
sending site, when rendered inside the real email templates.

article_card.html's site article link and author link (when
has_author_pages is on) must be tagged; trial_card.html's registry link
(always a third-party host) must never be tagged, since Umami can't see
those clicks.

Run:
  docker exec gregory python manage.py test subscriptions.tests.test_utm_host_restriction
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")

import django

django.setup()

from django.contrib.sites.models import Site
from django.template.loader import get_template
from django.test import TestCase

from gregory.models import Articles, Authors, Subject, Team, Trials
from organizations.models import Organization
from sitesettings.models import CustomSetting
from templates.emails.components.content_organizer import get_optimized_email_context


class UtmHostRestrictionTest(TestCase):
	def setUp(self):
		self.organization = Organization.objects.create(
			name="Utm Host Org", slug="utm-host-org"
		)
		self.team = Team.objects.create(
			name="Utm Host Team", organization=self.organization, slug="utm-host-team"
		)
		self.subject = Subject.objects.create(
			subject_name="Utm Host Subject",
			team=self.team,
			subject_slug="utm-host-subject",
		)
		self.site = Site.objects.create(domain="utm-host.example.com", name="UtmHost")
		self.author = Authors.objects.create(
			given_name="Jane", family_name="Doe", ORCID="0000-0002-7922-9785"
		)
		self.article = Articles.objects.create(
			title="Utm Host Article",
			doi="10.9999/utm-host-1",
			link="https://publisher.example.com/utm-host-1",
		)
		self.article.subjects.add(self.subject)
		self.article.authors.add(self.author)
		self.trial = Trials.objects.create(
			title="Utm Host Trial",
			link="https://clinicaltrials.gov/study/NCT-utm-host",
		)
		self.trial.subjects.add(self.subject)

	def _render(self, template_name, email_type, has_author_pages):
		custom_settings = CustomSetting.objects.create(
			site=self.site,
			title="Utm Host Site",
			has_author_pages=has_author_pages,
		)
		utm_params = {"utm_source": email_type, "utm_medium": "email"}
		context = get_optimized_email_context(
			email_type=email_type,
			articles=Articles.objects.filter(pk=self.article.pk),
			trials=Trials.objects.filter(pk=self.trial.pk),
			subscriber={"email": "admin@example.com"},
			site=self.site,
			custom_settings=custom_settings,
			utm_params=utm_params,
		)
		return get_template(template_name).render(context)

	def test_site_article_link_is_tagged(self):
		html = self._render(
			"emails/weekly_summary.html", "weekly_summary", has_author_pages=False
		)
		self.assertIn(
			f'href="https://utm-host.example.com/articles/{self.article.article_id}/?utm_source=weekly_summary&amp;utm_medium=email"',
			html,
		)

	def test_author_link_to_site_is_tagged_when_flag_on(self):
		# Author links always carry utm_content=author (see
		# gregory_tags.with_utm_content), overriding whatever content
		# value the base utm_params dict carries.
		html = self._render(
			"emails/weekly_summary.html", "weekly_summary", has_author_pages=True
		)
		self.assertIn(
			'href="https://utm-host.example.com/authors/0000-0002-7922-9785/?',
			html,
		)
		self.assertIn("utm_source=weekly_summary", html)
		self.assertIn("utm_content=author", html)

	def test_author_link_to_orcid_is_not_tagged(self):
		html = self._render(
			"emails/weekly_summary.html", "weekly_summary", has_author_pages=False
		)
		self.assertIn('href="https://orcid.org/0000-0002-7922-9785"', html)
		self.assertNotIn("orcid.org/0000-0002-7922-9785?", html)

	def test_trial_registry_link_is_never_tagged(self):
		html = self._render(
			"emails/weekly_summary.html", "weekly_summary", has_author_pages=False
		)
		self.assertIn(
			'href="https://clinicaltrials.gov/study/NCT-utm-host"', html
		)
		self.assertNotIn("clinicaltrials.gov/study/NCT-utm-host?", html)

	def test_admin_summary_original_article_link_is_never_tagged(self):
		# admin_summary always renders article_card.html with
		# show_admin_links=True, which links to the original publisher
		# (article.link) rather than the site's own article page.
		html = self._render(
			"emails/admin_summary.html", "admin_summary", has_author_pages=False
		)
		self.assertIn(
			'href="https://publisher.example.com/utm-host-1"', html
		)
		self.assertNotIn("publisher.example.com/utm-host-1?", html)

	def test_admin_summary_trial_registry_link_is_never_tagged(self):
		html = self._render(
			"emails/admin_summary.html", "admin_summary", has_author_pages=False
		)
		self.assertIn(
			'href="https://clinicaltrials.gov/study/NCT-utm-host"', html
		)
		self.assertNotIn("clinicaltrials.gov/study/NCT-utm-host?", html)
