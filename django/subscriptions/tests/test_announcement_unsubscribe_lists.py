"""
Tests for one-unsubscribe-link-per-list in announcement footers.

Before this change, ``send_announcement`` deduplicated recipients by email
and kept only the first list encountered, so a multi-list announcement
rendered a single "Unsubscribe from this list" link pointing at whichever
list happened to come first — ambiguous for anyone on more than one of the
announcement's lists.

Covers:
- send_announcement collects every matched list per subscriber and renders
  one named unsubscribe link per list (not just the first)
- a subscriber on several of the announcement's lists still receives
  exactly one email
- each rendered link resolves to the right list_id
- the shared footer used by weekly digests, admin summaries, and trial
  notifications is unaffected — one link, unchanged wording
"""

from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup
from django.contrib.sites.models import Site
from django.template.loader import render_to_string
from django.test import TestCase

from gregory.models import Team
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import (
	Announcement,
	AnnouncementRecipient,
	Lists,
	ListSubscription,
	Subscribers,
)
from subscriptions.utils.announcement_send import (
	render_announcement_email,
	send_announcement,
)

SEND_EMAIL_TARGET = "subscriptions.utils.announcement_send.send_email"


def _ok_response():
	r = MagicMock()
	r.status_code = 200
	r.json.return_value = {"MessageID": "test"}
	return r


class _MultiListAnnouncementBase(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.org = Organization.objects.create(name="Unsub Org")
		cls.team = Team.objects.create(
			organization=cls.org, name="Unsub Team", slug="unsub-team"
		)
		cls.site = Site.objects.get_or_create(
			id=31, defaults={"domain": "unsub.example.com", "name": "Unsub"}
		)[0]
		cls.site.domain = "unsub.example.com"
		cls.site.save()
		cls.cs = CustomSetting.objects.create(
			site=cls.site,
			title="Unsub CS",
			api_domain="api.unsub.example.com",
		)
		cls.list_a = Lists.objects.create(
			list_name="Project News", team=cls.team, site=cls.site
		)
		cls.list_b = Lists.objects.create(
			list_name="TEST", team=cls.team, site=cls.site
		)

	def _subscribe(self, sub, *lists):
		for lst in lists:
			ListSubscription.objects.create(subscriber=sub, list=lst, is_active=True)

	def _make_subscriber(self, email, first_name="Sub"):
		return Subscribers.objects.create(
			first_name=first_name, last_name="Test", email=email, active=True
		)

	def _make_announcement(self, *lists):
		ann = Announcement.objects.create(
			subject="Multi-list Announcement",
			body="<p>Body</p>",
			status="draft",
			organization=self.org,
		)
		ann.lists.add(*lists)
		return ann


class SendAnnouncementMultiListUnsubscribeTest(_MultiListAnnouncementBase):
	def test_subscriber_on_both_lists_gets_one_email_with_two_links(self):
		both = self._make_subscriber("both@example.com")
		self._subscribe(both, self.list_a, self.list_b)
		ann = self._make_announcement(self.list_a, self.list_b)

		with patch(SEND_EMAIL_TARGET, return_value=_ok_response()) as mock_send:
			summary = send_announcement(ann)

		# Exactly one email, not two.
		mock_send.assert_called_once()
		self.assertEqual(summary["sent"], 1)

		html = mock_send.call_args.kwargs["html"]
		soup = BeautifulSoup(html, "html.parser")
		links = soup.find_all("a", href=lambda h: h and "/list/" in h)
		texts = sorted(a.get_text() for a in links)
		self.assertEqual(
			texts,
			["Unsubscribe from Project News", "Unsubscribe from TEST"],
		)

		# Each link must resolve to the right list_id.
		href_a = next(a["href"] for a in links if "Project News" in a.get_text())
		href_b = next(a["href"] for a in links if "TEST" in a.get_text())
		self.assertIn(f"/list/{self.list_a.list_id}/", href_a)
		self.assertIn(f"/list/{self.list_b.list_id}/", href_b)

	def test_subscriber_on_only_one_list_gets_one_link(self):
		only_a = self._make_subscriber("only_a@example.com")
		self._subscribe(only_a, self.list_a)
		ann = self._make_announcement(self.list_a, self.list_b)

		with patch(SEND_EMAIL_TARGET, return_value=_ok_response()) as mock_send:
			send_announcement(ann)

		html = mock_send.call_args.kwargs["html"]
		soup = BeautifulSoup(html, "html.parser")
		links = soup.find_all("a", href=lambda h: h and "/list/" in h)
		self.assertEqual(len(links), 1)
		self.assertEqual(links[0].get_text(), "Unsubscribe from Project News")
		self.assertIn(f"/list/{self.list_a.list_id}/", links[0]["href"])

	def test_attribution_recipient_row_still_single_list(self):
		"""AnnouncementRecipient.list stays a single FK — the link set is a
		render concern, not a storage one."""
		both = self._make_subscriber("both2@example.com")
		self._subscribe(both, self.list_a, self.list_b)
		ann = self._make_announcement(self.list_a, self.list_b)

		with patch(SEND_EMAIL_TARGET, return_value=_ok_response()):
			send_announcement(ann)

		recipient = AnnouncementRecipient.objects.get(announcement=ann, subscriber=both)
		self.assertIn(recipient.list_id, [self.list_a.list_id, self.list_b.list_id])


class RenderAnnouncementEmailUnsubscribeListsTest(_MultiListAnnouncementBase):
	"""Direct unit tests of render_announcement_email's unsubscribe_lists arg."""

	def test_no_unsubscribe_lists_falls_back_to_single_list_wording(self):
		ann = self._make_announcement(self.list_a)
		sub = self._make_subscriber("solo@example.com")
		html = render_announcement_email(
			ann,
			subscriber=sub,
			site=self.site,
			list_id=self.list_a.list_id,
			custom_settings=self.cs,
		)
		soup = BeautifulSoup(html, "html.parser")
		self.assertIsNotNone(soup.find("a", string="Unsubscribe from this list"))


class DigestFooterUnchangedTest(TestCase):
	"""Regression guard: weekly digest, admin summary, and trial notification
	must render exactly as before — one "Unsubscribe from this list" link,
	since they only ever pass list_id, never unsubscribe_lists."""

	@classmethod
	def setUpTestData(cls):
		cls.org = Organization.objects.create(name="Digest Org")
		cls.team = Team.objects.create(
			organization=cls.org, name="Digest Team", slug="digest-team"
		)
		cls.site = Site.objects.get_or_create(
			id=32, defaults={"domain": "digest.example.com", "name": "Digest"}
		)[0]
		cls.lst = Lists.objects.create(
			list_name="Digest List", team=cls.team, site=cls.site
		)
		cls.sub = Subscribers.objects.create(
			first_name="Digest",
			last_name="Subscriber",
			email="digest@example.com",
			active=True,
		)

	def _base_context(self):
		return {
			"subscriber": self.sub,
			"site": self.site,
			"list_id": self.lst.list_id,
			"unsubscribe_base_url": "https://digest.example.com",
			"title": "Gregory AI",
			"articles": [],
			"trials": [],
			"additional_articles": [],
			"additional_trials": [],
			"email_type": "weekly_summary",
			"show_date": True,
		}

	def _assert_single_link(self, html):
		soup = BeautifulSoup(html, "html.parser")
		links = soup.find_all("a", href=lambda h: h and "/list/" in h)
		self.assertEqual(len(links), 1)
		self.assertEqual(links[0].get_text(), "Unsubscribe from this list")
		self.assertIn(f"/list/{self.lst.list_id}/", links[0]["href"])

	def test_weekly_summary_footer_unchanged(self):
		context = self._base_context()
		context["organization"] = self.org
		html = render_to_string("emails/weekly_summary.html", context)
		self._assert_single_link(html)

	def test_admin_summary_footer_unchanged(self):
		context = self._base_context()
		context["email_type"] = "admin_summary"
		html = render_to_string("emails/admin_summary.html", context)
		self._assert_single_link(html)

	def test_trial_notification_footer_unchanged(self):
		context = self._base_context()
		context["email_type"] = "trial_notification"
		html = render_to_string("emails/trial_notification.html", context)
		self._assert_single_link(html)
