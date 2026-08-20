"""
Tests for the shared UTM scheme (build_utm_params) wired into all four
senders: utm_source matches the Postmark tag, utm_campaign matches the
list's stable slug, and utm_content is never a subscriber identifier.

Before this change, only send_weekly_summary tagged its links, and it
used utm_content=subscriber_<id> — the subscriber's primary key, landing
in Umami on every click. send_admin_summary and send_trials_notification
tagged nothing at all, so their clicks showed up as direct traffic.

Run:
  docker exec gregory python manage.py test subscriptions.tests.test_utm_scheme
"""

import os
from io import StringIO
from unittest.mock import MagicMock, patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")

import django

django.setup()

from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase
from django.utils.timezone import now

from gregory.models import Articles, Subject, Team, Trials
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import (
	Announcement,
	Lists,
	ListSubscription,
	Subscribers,
)
from subscriptions.utils.utm import build_utm_params


def _mock_ok_result():
	result = MagicMock(status_code=200)
	result.json.return_value = {"ErrorCode": 0, "Message": "OK"}
	return result


class BuildUtmParamsTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Utm Scheme Org")
		self.team = Team.objects.create(
			organization=self.org, name="Utm Scheme Team", slug="utm-scheme-team"
		)

	def test_uses_list_slug_as_campaign(self):
		lst = Lists.objects.create(list_name="Weekly Neuro Digest", team=self.team)
		params = build_utm_params("weekly_summary", lst, "article_card")
		self.assertEqual(params["utm_source"], "weekly_summary")
		self.assertEqual(params["utm_campaign"], lst.utm_campaign_slug)
		self.assertEqual(params["utm_medium"], "email")
		self.assertEqual(params["utm_content"], "article_card")

	def test_falls_back_to_slugified_name_when_slug_blank(self):
		lst = Lists.objects.create(list_name="Fallback List", team=self.team)
		lst.utm_campaign_slug = ""
		# Bypass save() so the blank slug survives, simulating a row that
		# predates the field.
		Lists.objects.filter(pk=lst.pk).update(utm_campaign_slug="")
		lst.refresh_from_db()
		self.assertEqual(lst.utm_campaign_slug, "")

		params = build_utm_params("weekly_summary", lst, "article_card")
		self.assertEqual(params["utm_campaign"], "fallback-list")


class SenderUtmSchemeTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Sender Utm Org")
		self.team = Team.objects.create(
			organization=self.org, name="Sender Utm Team", slug="sender-utm-team"
		)
		self.subject = Subject.objects.create(
			subject_name="Sender Utm Subject",
			team=self.team,
			subject_slug="sender-utm-subject",
			auto_predict=True,
			ml_consensus_type="any",
		)
		self.site = Site.objects.get_or_create(
			id=61, defaults={"domain": "sender-utm.example.com", "name": "SenderUtm"}
		)[0]
		self.custom_settings = CustomSetting.objects.get_or_create(
			site=self.site,
			defaults={
				"title": "Sender Utm Site",
				"postmark_api_token": "test-token",
				"postmark_api_url": "https://api.postmarkapp.com/email",
			},
		)[0]

	def _make_subscriber(self, email):
		return Subscribers.objects.create(
			first_name="Reader", last_name="Test", email=email, active=True
		)

	def test_weekly_summary_utm_source_and_campaign(self):
		digest_list = Lists.objects.create(
			list_name="Sender Weekly List",
			weekly_digest=True,
			team=self.team,
			site=self.site,
			ml_threshold=0.0,
		)
		digest_list.subjects.add(self.subject)
		subscriber = self._make_subscriber("weekly-utm@example.com")
		ListSubscription.objects.create(
			subscriber=subscriber, list=digest_list, is_active=True
		)
		article = Articles.objects.create(
			title="Weekly Utm Article", doi="10.1/weekly-utm"
		)
		article.subjects.add(self.subject)

		with patch(
			"subscriptions.management.commands.send_weekly_summary.send_email",
			return_value=_mock_ok_result(),
		) as mock_send_email:
			call_command(
				"send_weekly_summary", stdout=StringIO(), all_articles=True
			)

		self.assertTrue(mock_send_email.called)
		_, kwargs = mock_send_email.call_args
		html = kwargs["html"]
		self.assertIn("utm_source=weekly_summary", html)
		self.assertIn(f"utm_campaign={digest_list.utm_campaign_slug}", html)
		# "For the complete archive, visit <site>" was previously untagged.
		self.assertIn(
			f'href="https://{self.site.domain}/?utm_medium=email', html
		)
		self.assertIn("utm_content=footer", html)

	def test_admin_summary_utm_source_and_campaign(self):
		admin_list = Lists.objects.create(
			list_name="Sender Admin List",
			admin_summary=True,
			team=self.team,
			site=self.site,
		)
		admin_list.subjects.add(self.subject)
		subscriber = self._make_subscriber("admin-utm@example.com")
		subscriber.subscriptions.add(admin_list)
		article = Articles.objects.create(
			title="Admin Utm Article",
			link="https://publisher.example.com/admin-utm",
			doi="10.1/admin-utm",
		)
		article.subjects.add(self.subject)

		with patch(
			"subscriptions.management.commands.send_admin_summary.send_email",
			return_value=_mock_ok_result(),
		) as mock_send_email:
			call_command("send_admin_summary", stdout=StringIO())

		self.assertTrue(mock_send_email.called)
		_, kwargs = mock_send_email.call_args
		html = kwargs["html"]
		self.assertIn("utm_source=admin_summary", html)
		self.assertIn(f"utm_campaign={admin_list.utm_campaign_slug}", html)

	def test_trial_notification_utm_source_and_campaign(self):
		lst = Lists.objects.create(
			list_name="Sender Trial List",
			team=self.team,
			site=self.site,
			clinical_trials_notifications=True,
		)
		lst.subjects.add(self.subject)
		subscriber = self._make_subscriber("trial-utm@example.com")
		ListSubscription.objects.create(
			subscriber=subscriber, list=lst, is_active=True
		)
		trial = Trials.objects.create(
			title="Trial Utm Trial",
			link="https://clinicaltrials.gov/study/NCT-trial-utm",
			discovery_date=now(),
		)
		trial.subjects.add(self.subject)

		with patch(
			"subscriptions.management.commands.send_trials_notification.send_email",
			return_value=_mock_ok_result(),
		) as mock_send_email:
			call_command("send_trials_notification", stdout=StringIO())

		self.assertTrue(mock_send_email.called)
		_, kwargs = mock_send_email.call_args
		html = kwargs["html"]
		self.assertIn("utm_source=trial_notification", html)
		self.assertIn(f"utm_campaign={lst.utm_campaign_slug}", html)
		# "Browse all available clinical trials at <site>" was previously untagged.
		self.assertIn(
			f'href="https://{self.site.domain}/?utm_medium=email', html
		)
		self.assertIn("utm_content=footer", html)

	def test_no_subscriber_identifier_in_weekly_summary_href(self):
		# Regression guard: utm_content used to be subscriber_<id>, the
		# subscriber's primary key, in every tracked link.
		digest_list = Lists.objects.create(
			list_name="No Subscriber Id List",
			weekly_digest=True,
			team=self.team,
			site=self.site,
			ml_threshold=0.0,
		)
		digest_list.subjects.add(self.subject)
		subscriber = self._make_subscriber("no-subscriber-id@example.com")
		ListSubscription.objects.create(
			subscriber=subscriber, list=digest_list, is_active=True
		)
		article = Articles.objects.create(
			title="No Subscriber Id Article", doi="10.1/no-subscriber-id"
		)
		article.subjects.add(self.subject)

		with patch(
			"subscriptions.management.commands.send_weekly_summary.send_email",
			return_value=_mock_ok_result(),
		) as mock_send_email:
			call_command(
				"send_weekly_summary", stdout=StringIO(), all_articles=True
			)

		self.assertTrue(mock_send_email.called)
		_, kwargs = mock_send_email.call_args
		html = kwargs["html"]
		self.assertNotIn("subscriber_", html)
		self.assertNotIn(f"utm_content=subscriber_{subscriber.subscriber_id}", html)


class AnnouncementUtmSchemeTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Announcement Utm Org")
		self.team = Team.objects.create(
			organization=self.org,
			name="Announcement Utm Team",
			slug="announcement-utm-team",
		)
		self.site = Site.objects.get_or_create(
			id=62,
			defaults={"domain": "announcement-utm.example.com", "name": "AnnUtm"},
		)[0]
		self.custom_settings = CustomSetting.objects.get_or_create(
			site=self.site,
			defaults={
				"title": "Announcement Utm Site",
				"api_domain": "api.announcement-utm.example.com",
				"postmark_api_token": "test-token",
				"postmark_api_url": "https://api.postmarkapp.com/email",
				"website_url": "https://announcement-utm.example.com",
			},
		)[0]
		self.lst = Lists.objects.create(
			list_name="Announcement Utm List", team=self.team, site=self.site
		)
		self.subscriber = Subscribers.objects.create(
			first_name="Ann",
			last_name="Reader",
			email="announcement-utm@example.com",
			active=True,
		)
		ListSubscription.objects.create(
			subscriber=self.subscriber, list=self.lst, is_active=True
		)

	def test_announcement_body_link_to_site_is_tagged(self):
		announcement = Announcement.objects.create(
			subject="Utm Announcement",
			body='<p>Read <a href="https://announcement-utm.example.com/articles/1/">this</a>.</p>',
			status="queued",
			organization=self.org,
		)
		announcement.lists.add(self.lst)

		with patch(
			"subscriptions.utils.announcement_send.send_email",
			return_value=_mock_ok_result(),
		) as mock_send_email:
			call_command("send_announcement", stdout=StringIO())

		self.assertTrue(mock_send_email.called)
		_, kwargs = mock_send_email.call_args
		html = kwargs["html"]
		self.assertIn("utm_source=announcement", html)
		self.assertIn(f"utm_campaign={self.lst.utm_campaign_slug}", html)
		self.assertIn("utm_content=announcement_body", html)

	def test_announcement_footer_website_link_is_tagged(self):
		announcement = Announcement.objects.create(
			subject="Utm Announcement Footer",
			body="<p>Hello.</p>",
			status="queued",
			organization=self.org,
		)
		announcement.lists.add(self.lst)

		with patch(
			"subscriptions.utils.announcement_send.send_email",
			return_value=_mock_ok_result(),
		) as mock_send_email:
			call_command("send_announcement", stdout=StringIO())

		self.assertTrue(mock_send_email.called)
		_, kwargs = mock_send_email.call_args
		html = kwargs["html"]
		self.assertIn("utm_content=footer", html)
