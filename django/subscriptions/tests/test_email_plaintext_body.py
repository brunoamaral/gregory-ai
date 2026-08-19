"""
Regression tests for the plain-text alternative body of the three
structured-content senders (weekly digest, admin summary, trial
notification).

These used to build the text part with strip_tags(html_content), which
(a) prepends base_email.html's two <style> blocks verbatim as raw CSS
because strip_tags keeps element content, and (b) drops every href, so
the text part carried no links at all. Each sender now renders a
dedicated emails/*.txt template from the same context instead.
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
from subscriptions.models import Lists, ListSubscription, Subscribers


def _mock_ok_result():
	result = MagicMock(status_code=200)
	result.json.return_value = {"ErrorCode": 0, "Message": "OK"}
	return result


def _assert_clean_text_body(testcase, text):
	testcase.assertNotIn("{", text)
	testcase.assertNotIn("color:", text)
	testcase.assertIn("https://", text)


class WeeklySummaryPlainTextTests(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(
			name="Plaintext Weekly Org", slug="plaintext-weekly-org"
		)
		self.team = Team.objects.create(
			name="Plaintext Weekly Team",
			organization=self.org,
			slug="plaintext-weekly-team",
		)
		self.subject = Subject.objects.create(
			subject_name="Plaintext Subject",
			team=self.team,
			subject_slug="plaintext-subject",
		)
		self.site = Site.objects.get_or_create(
			id=51, defaults={"domain": "plaintext.example.com", "name": "Plaintext"}
		)[0]
		self.custom_settings = CustomSetting.objects.get_or_create(
			site=self.site,
			defaults={
				"title": "Plaintext Site",
				"postmark_api_token": "test-token",
				"postmark_api_url": "https://api.postmarkapp.com/email",
			},
		)[0]
		self.digest_list = Lists.objects.create(
			list_name="Plaintext Weekly List",
			weekly_digest=True,
			team=self.team,
			site=self.site,
			ml_threshold=0.0,
		)
		self.digest_list.subjects.add(self.subject)
		self.subscriber = Subscribers.objects.create(
			first_name="Text",
			last_name="Reader",
			email="text-reader@example.com",
			active=True,
		)
		ListSubscription.objects.create(
			subscriber=self.subscriber, list=self.digest_list, is_active=True
		)
		self.article = Articles.objects.create(
			title="A Plaintext Body Article",
			link="https://publisher.example.com/plaintext-article",
			doi="10.1234/plaintext-article",
		)
		self.article.subjects.add(self.subject)

	def _run_and_capture_text(self):
		with patch(
			"subscriptions.management.commands.send_weekly_summary.send_email",
			return_value=_mock_ok_result(),
		) as mock_send_email:
			out = StringIO()
			call_command("send_weekly_summary", stdout=out, all_articles=True)

		self.assertTrue(mock_send_email.called)
		_, kwargs = mock_send_email.call_args
		return kwargs["text"]

	def test_text_body_has_no_css_and_a_link(self):
		text = self._run_and_capture_text()
		_assert_clean_text_body(self, text)

	def test_text_body_contains_article_title(self):
		text = self._run_and_capture_text()
		self.assertIn("A Plaintext Body Article", text)


class AdminSummaryPlainTextTests(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(
			name="Plaintext Admin Org", slug="plaintext-admin-org"
		)
		self.team = Team.objects.create(
			name="Plaintext Admin Team",
			organization=self.org,
			slug="plaintext-admin-team",
		)
		self.subject = Subject.objects.create(
			subject_name="Plaintext Admin Subject",
			team=self.team,
			subject_slug="plaintext-admin-subject",
			auto_predict=True,
			ml_consensus_type="any",
		)
		self.site = Site.objects.get_or_create(
			id=52,
			defaults={"domain": "plaintext-admin.example.com", "name": "PlaintextAdmin"},
		)[0]
		self.custom_settings = CustomSetting.objects.get_or_create(
			site=self.site,
			defaults={
				"title": "Plaintext Admin Site",
				"postmark_api_token": "test-token",
				"postmark_api_url": "https://api.postmarkapp.com/email",
			},
		)[0]
		self.subscriber = Subscribers.objects.create(
			first_name="Admin",
			last_name="Reader",
			email="admin-reader@example.com",
			active=True,
		)
		self.admin_list = Lists.objects.create(
			list_name="Plaintext Admin List",
			admin_summary=True,
			team=self.team,
			site=self.site,
		)
		self.admin_list.subjects.add(self.subject)
		self.subscriber.subscriptions.add(self.admin_list)
		self.article = Articles.objects.create(
			title="An Admin Plaintext Article",
			link="https://publisher.example.com/admin-plaintext-article",
			doi="10.1234/admin-plaintext-article",
		)
		self.article.subjects.add(self.subject)

	def _run_and_capture_text(self):
		with patch(
			"subscriptions.management.commands.send_admin_summary.send_email",
			return_value=_mock_ok_result(),
		) as mock_send_email:
			out = StringIO()
			call_command("send_admin_summary", stdout=out)

		self.assertTrue(mock_send_email.called)
		_, kwargs = mock_send_email.call_args
		return kwargs["text"]

	def test_text_body_has_no_css_and_a_link(self):
		text = self._run_and_capture_text()
		_assert_clean_text_body(self, text)

	def test_text_body_contains_article_title(self):
		text = self._run_and_capture_text()
		self.assertIn("An Admin Plaintext Article", text)


class TrialNotificationPlainTextTests(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(
			name="Plaintext Trial Org", slug="plaintext-trial-org"
		)
		self.team = Team.objects.create(
			name="Plaintext Trial Team",
			organization=self.org,
			slug="plaintext-trial-team",
		)
		self.subject = Subject.objects.create(
			subject_name="Plaintext Trial Subject",
			team=self.team,
			subject_slug="plaintext-trial-subject",
		)
		self.site = Site.objects.get_or_create(
			id=53,
			defaults={"domain": "plaintext-trial.example.com", "name": "PlaintextTrial"},
		)[0]
		self.custom_settings = CustomSetting.objects.get_or_create(
			site=self.site,
			defaults={
				"title": "Plaintext Trial Site",
				"postmark_api_token": "test-token",
				"postmark_api_url": "https://api.postmarkapp.com/email",
			},
		)[0]
		self.lst = Lists.objects.create(
			list_name="Plaintext Trial List",
			team=self.team,
			site=self.site,
			clinical_trials_notifications=True,
		)
		self.lst.subjects.add(self.subject)
		self.subscriber = Subscribers.objects.create(
			first_name="Trial",
			last_name="Reader",
			email="trial-reader@example.com",
			active=True,
		)
		ListSubscription.objects.create(
			subscriber=self.subscriber, list=self.lst, is_active=True
		)
		self.trial = Trials.objects.create(
			title="A Plaintext Body Trial",
			link="https://registry.example.com/plaintext-trial",
			discovery_date=now(),
		)
		self.trial.subjects.add(self.subject)

	def _run_and_capture_text(self):
		with patch(
			"subscriptions.management.commands.send_trials_notification.send_email",
			return_value=_mock_ok_result(),
		) as mock_send_email:
			out = StringIO()
			call_command("send_trials_notification", stdout=out)

		self.assertTrue(mock_send_email.called)
		_, kwargs = mock_send_email.call_args
		return kwargs["text"]

	def test_text_body_has_no_css_and_a_link(self):
		text = self._run_and_capture_text()
		_assert_clean_text_body(self, text)

	def test_text_body_contains_trial_title(self):
		text = self._run_and_capture_text()
		self.assertIn("A Plaintext Body Trial", text)
