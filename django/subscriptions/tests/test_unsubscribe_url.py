"""
Tests for the unsubscribe_base_url logic in send_weekly_summary.

The command derives the URL as:

    api_domain           = CustomSetting.api_domain (stripped) if non-empty
    domain               = api_domain or site.domain (stripped)
    scheme               = 'http' if domain in ('localhost', '127.0.0.1') else 'https'
    unsubscribe_base_url = f"{scheme}://{domain}"

Scenarios:
1. api_domain set on CustomSetting  → api_domain is used
2. api_domain empty / not set       → site.domain used as fallback
3. site.domain == 'localhost'       → http:// scheme
4. site.domain == '127.0.0.1'      → http:// scheme
"""
import os
import django
from datetime import timedelta
from unittest.mock import patch, MagicMock

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase
from django.utils.timezone import now

from gregory.models import Subject, Articles, Team
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import Lists, Subscribers, ListSubscription


def _ok_response():
	"""Fake successful Postmark HTTP response."""
	mock = MagicMock()
	mock.status_code = 200
	mock.json.return_value = {"ErrorCode": 0, "Message": "OK"}
	return mock


class TestUnsubscribeBaseUrl(TestCase):
	"""Ensure send_weekly_summary injects the correct unsubscribe_base_url."""

	# ------------------------------------------------------------------ setup

	def setUp(self):
		self.organization = Organization.objects.create(
			name="Unsub Test Org", slug="unsub-test-org"
		)
		self.team = Team.objects.create(
			name="Unsub Test Team",
			organization=self.organization,
			slug="unsub-test-team",
		)
		self.subject = Subject.objects.create(
			subject_name="Unsub Subject",
			team=self.team,
			subject_slug="unsub-subject",
		)

		# Site must exist before creating the list so that the Lists.save()
		# auto-populate fallback (Site.objects.get_current()) resolves to it.
		self.site, _ = Site.objects.get_or_create(
			id=1,
			defaults={"domain": "testserver.example.com", "name": "Test Site"},
		)
		self.site.domain = "testserver.example.com"
		self.site.save()

		# CustomSetting linked to site id=1; api_domain starts empty.
		self.custom_settings, _ = CustomSetting.objects.get_or_create(
			site=self.site,
			defaults={
				"title": "Test Site",
				"api_domain": "",
			},
		)
		self.custom_settings.api_domain = ""
		self.custom_settings.save()

		# Lists.save() picks up site via get_current() → site id=1.
		self.digest_list = Lists.objects.create(
			list_name="Unsub Test Digest",
			weekly_digest=True,
			team=self.team,
			ml_threshold=0.5,
		)
		self.digest_list.subjects.add(self.subject)

		self.subscriber = Subscribers.objects.create(
			first_name="Bob",
			last_name="Unsub",
			email="bob@unsub-test.example.com",
			active=True,
		)
		ListSubscription.objects.create(
			subscriber=self.subscriber,
			list=self.digest_list,
			is_active=True,
		)

		# One recent article so the command has content to send.
		self.article = Articles.objects.create(
			title="Unsubscribe URL test article",
			discovery_date=now() - timedelta(days=1),
			doi="10.9999/unsub-url-test",
		)
		self.article.subjects.add(self.subject)

	# ---------------------------------------------------------------- helpers

	def _captured_html(self, mock_send_email):
		"""Return the html kwarg from the first send_email call."""
		self.assertTrue(mock_send_email.called, "send_email was never called")
		return mock_send_email.call_args[1].get("html", "")

	def _run(self, mock_send_email):
		mock_send_email.return_value = _ok_response()
		call_command("send_weekly_summary", all_articles=True)

	# ------------------------------------------------------------------ tests

	@patch(
		"subscriptions.management.commands.send_weekly_summary.get_postmark_credentials",
		return_value=("test-token", "https://api.postmarkapp.com/email"),
	)
	@patch("subscriptions.management.commands.send_weekly_summary.send_email")
	def test_api_domain_used_when_set(self, mock_send_email, _mock_creds):
		"""When api_domain is set on CustomSetting, unsubscribe links use it."""
		self.custom_settings.api_domain = "api.example.com"
		self.custom_settings.save()

		self._run(mock_send_email)

		html = self._captured_html(mock_send_email)
		self.assertIn(
			"https://api.example.com/subscriptions/unsubscribe/",
			html,
			"Unsubscribe link should use CustomSetting.api_domain when set",
		)
		self.assertNotIn(
			"https://testserver.example.com/subscriptions/unsubscribe/",
			html,
			"Unsubscribe link must NOT use site.domain when api_domain is set",
		)

	@patch(
		"subscriptions.management.commands.send_weekly_summary.get_postmark_credentials",
		return_value=("test-token", "https://api.postmarkapp.com/email"),
	)
	@patch("subscriptions.management.commands.send_weekly_summary.send_email")
	def test_site_domain_used_when_api_domain_empty(self, mock_send_email, _mock_creds):
		"""When api_domain is empty, unsubscribe links fall back to site.domain."""
		self.custom_settings.api_domain = ""
		self.custom_settings.save()

		self._run(mock_send_email)

		html = self._captured_html(mock_send_email)
		self.assertIn(
			"https://testserver.example.com/subscriptions/unsubscribe/",
			html,
			"Unsubscribe link should fall back to site.domain when api_domain is empty",
		)

	@patch(
		"subscriptions.management.commands.send_weekly_summary.get_postmark_credentials",
		return_value=("test-token", "https://api.postmarkapp.com/email"),
	)
	@patch("subscriptions.management.commands.send_weekly_summary.send_email")
	def test_http_scheme_for_localhost(self, mock_send_email, _mock_creds):
		"""When the resolved domain is 'localhost', the scheme must be http://."""
		self.site.domain = "localhost"
		self.site.save()
		self.custom_settings.api_domain = ""
		self.custom_settings.save()

		self._run(mock_send_email)

		html = self._captured_html(mock_send_email)
		self.assertIn(
			"http://localhost/subscriptions/unsubscribe/",
			html,
			"Unsubscribe link should use http:// for localhost",
		)
		self.assertNotIn(
			"https://localhost/subscriptions/unsubscribe/",
			html,
			"Unsubscribe link must NOT use https:// for localhost",
		)

	@patch(
		"subscriptions.management.commands.send_weekly_summary.get_postmark_credentials",
		return_value=("test-token", "https://api.postmarkapp.com/email"),
	)
	@patch("subscriptions.management.commands.send_weekly_summary.send_email")
	def test_http_scheme_for_loopback_ip(self, mock_send_email, _mock_creds):
		"""When the resolved domain is '127.0.0.1', the scheme must be http://."""
		self.site.domain = "127.0.0.1"
		self.site.save()
		self.custom_settings.api_domain = ""
		self.custom_settings.save()

		self._run(mock_send_email)

		html = self._captured_html(mock_send_email)
		self.assertIn(
			"http://127.0.0.1/subscriptions/unsubscribe/",
			html,
			"Unsubscribe link should use http:// for 127.0.0.1",
		)
		self.assertNotIn(
			"https://127.0.0.1/subscriptions/unsubscribe/",
			html,
			"Unsubscribe link must NOT use https:// for 127.0.0.1",
		)
