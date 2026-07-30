"""
Tests for restoring the announcement footer's legal links.

``render_announcement_email`` used to hardcode privacy_policy_url/terms_url
to "", so those two footer links silently disappeared for announcements
only, while every other email type populated them from CustomSetting.
"""

from bs4 import BeautifulSoup
from django.contrib.sites.models import Site
from django.test import TestCase

from gregory.models import Team
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import Announcement, Lists, Subscribers
from subscriptions.utils.announcement_send import render_announcement_email


class AnnouncementLegalLinksTest(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.org = Organization.objects.create(name="Legal Org")
		cls.team = Team.objects.create(
			organization=cls.org, name="Legal Team", slug="legal-team"
		)
		cls.site = Site.objects.get_or_create(
			id=33, defaults={"domain": "legal.example.com", "name": "Legal"}
		)[0]
		cls.cs = CustomSetting.objects.create(
			site=cls.site,
			title="Legal CS",
			privacy_policy_url="https://legal.example.com/privacy",
			terms_url="https://legal.example.com/terms",
		)
		cls.lst = Lists.objects.create(
			list_name="Legal List", team=cls.team, site=cls.site
		)
		cls.sub = Subscribers.objects.create(
			first_name="Legal",
			last_name="Subscriber",
			email="legal@example.com",
			active=True,
		)
		cls.ann = Announcement.objects.create(
			subject="Legal Announcement",
			body="<p>Body</p>",
			status="draft",
			organization=cls.org,
		)
		cls.ann.lists.add(cls.lst)

	def test_privacy_and_terms_links_present(self):
		html = render_announcement_email(
			self.ann,
			subscriber=self.sub,
			site=self.site,
			list_id=self.lst.list_id,
			custom_settings=self.cs,
		)
		soup = BeautifulSoup(html, "html.parser")
		privacy = soup.find("a", string="Privacy Policy")
		terms = soup.find("a", string="Terms of Service")
		self.assertIsNotNone(privacy)
		self.assertIsNotNone(terms)
		self.assertEqual(privacy["href"], "https://legal.example.com/privacy")
		self.assertEqual(terms["href"], "https://legal.example.com/terms")

	def test_no_custom_settings_omits_links_without_error(self):
		html = render_announcement_email(
			self.ann,
			subscriber=self.sub,
			site=self.site,
			list_id=self.lst.list_id,
			custom_settings=None,
		)
		soup = BeautifulSoup(html, "html.parser")
		self.assertIsNone(soup.find("a", string="Privacy Policy"))
		self.assertIsNone(soup.find("a", string="Terms of Service"))
