"""
Tests for Postmark suppression handling (audit finding 3):

- subscriptions.utils.postmark.classify_postmark_response
- subscriptions.utils.suppression.deactivate_subscribers
- wiring in the three send commands: a 406 (suppressed recipient) triggers a
  global opt-out and the subscriber is never retried; a connection error is
  recorded and the loop continues; the send_admin_summary truthiness bug
  (falsy 422 Response treated as "no response") is fixed.
"""

import json
import os
from datetime import timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.tests.test_settings")

import django

django.setup()

import requests
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from gregory.models import Subject, Team, Trials
from organizations.models import Organization
from sitesettings.models import CustomSetting
from subscriptions.models import (
	FailedNotification,
	ListSubscription,
	Lists,
	SentTrialNotification,
	Subscribers,
)
from subscriptions.utils.postmark import (
	POSTMARK_INACTIVE_RECIPIENT,
	classify_postmark_response,
)
from subscriptions.utils.suppression import deactivate_subscribers


def _build_response(status_code, body):
	"""Build a real requests.Response so the __bool__ truthiness trap is
	genuinely exercised (Response.__bool__ is self.ok, so a 422 is falsy)."""
	resp = requests.Response()
	resp.status_code = status_code
	resp._content = json.dumps(body).encode("utf-8")
	return resp


class ClassifyPostmarkResponseTest(TestCase):
	def test_200_error_code_zero_is_delivered(self):
		resp = _build_response(200, {"ErrorCode": 0, "Message": "OK"})
		delivered, error_code, detail = classify_postmark_response(resp)
		self.assertTrue(delivered)
		self.assertEqual(error_code, 0)

	def test_200_nonzero_error_code_is_not_delivered(self):
		resp = _build_response(200, {"ErrorCode": 300, "Message": "Bad body"})
		delivered, error_code, detail = classify_postmark_response(resp)
		self.assertFalse(delivered)
		self.assertEqual(error_code, 300)

	def test_falsy_422_response_is_classified_correctly(self):
		resp = _build_response(
			422, {"ErrorCode": POSTMARK_INACTIVE_RECIPIENT, "Message": "Inactive"}
		)
		# Prove the truthiness trap is real: requests.Response.__bool__ is
		# self.ok, so a 422 response is falsy.
		self.assertFalse(bool(resp))

		delivered, error_code, detail = classify_postmark_response(resp)
		self.assertFalse(delivered)
		self.assertEqual(error_code, POSTMARK_INACTIVE_RECIPIENT)
		self.assertIn("422", detail)

	def test_500_is_not_delivered(self):
		resp = _build_response(500, {"Message": "Internal error"})
		delivered, error_code, detail = classify_postmark_response(resp)
		self.assertFalse(delivered)
		self.assertIn("500", detail)

	def test_none_result(self):
		delivered, error_code, detail = classify_postmark_response(None)
		self.assertFalse(delivered)
		self.assertIsNone(error_code)
		self.assertEqual(detail, "No response from Postmark")

	def test_dict_form(self):
		delivered, error_code, detail = classify_postmark_response(
			{"status_code": 200, "ErrorCode": 0, "Message": "OK"}
		)
		self.assertTrue(delivered)
		self.assertEqual(error_code, 0)

	def test_dict_form_error(self):
		delivered, error_code, detail = classify_postmark_response(
			{"status_code": 422, "ErrorCode": POSTMARK_INACTIVE_RECIPIENT}
		)
		self.assertFalse(delivered)
		self.assertEqual(error_code, POSTMARK_INACTIVE_RECIPIENT)


class DeactivateSubscribersTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Suppress Org", slug="suppress-org")
		self.team = Team.objects.create(
			name="Suppress Team", organization=self.org, slug="suppress-team"
		)
		self.lst = Lists.objects.create(list_name="Suppress List", team=self.team)
		self.subscriber = Subscribers.objects.create(
			first_name="Sup",
			last_name="Tester",
			email="suppress@example.com",
			active=True,
		)
		self.sub = ListSubscription.objects.create(
			subscriber=self.subscriber, list=self.lst, is_active=True
		)

	def test_deactivates_subscriber_and_subscriptions(self):
		subs_updated, subscriptions_updated = deactivate_subscribers(
			[self.subscriber.pk], reason="hard bounce"
		)
		self.subscriber.refresh_from_db()
		self.sub.refresh_from_db()

		self.assertEqual(subs_updated, 1)
		self.assertEqual(subscriptions_updated, 1)
		self.assertFalse(self.subscriber.active)
		self.assertFalse(self.sub.is_active)
		self.assertIsNotNone(self.sub.unsubscribed_at)

	def test_preserves_existing_unsubscribed_at(self):
		earlier = timezone.now() - timedelta(days=5)
		self.sub.unsubscribed_at = earlier
		self.sub.save(update_fields=["unsubscribed_at"])

		deactivate_subscribers([self.subscriber.pk])

		self.sub.refresh_from_db()
		self.assertEqual(self.sub.unsubscribed_at, earlier)


class BaseSuppressionCommandTestCase(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name="Bounce Org", slug="bounce-org")
		self.team = Team.objects.create(
			name="Bounce Team", organization=self.org, slug="bounce-team"
		)
		self.subject = Subject.objects.create(
			subject_name="Bounce Subject", team=self.team, subject_slug="bounce-subject"
		)
		self.site = Site.objects.get_or_create(
			id=1, defaults={"domain": "testserver", "name": "Test Site"}
		)[0]
		self.custom_settings = CustomSetting.objects.get_or_create(
			site=self.site,
			defaults={
				"title": "Test Site",
				"postmark_api_token": "test-token",
				"postmark_api_url": "https://api.postmarkapp.com/email",
			},
		)[0]
		self.lst = Lists.objects.create(
			list_name="Bounce Trial List",
			team=self.team,
			clinical_trials_notifications=True,
			list_email_subject="New Trials",
		)
		self.lst.subjects.add(self.subject)

	def _make_trial(self, title):
		trial = Trials.objects.create(
			title=title, link=f"https://example.com/trials/{title.replace(' ', '-')}"
		)
		Trials.objects.filter(pk=trial.pk).update(discovery_date=timezone.now())
		trial.refresh_from_db()
		trial.subjects.add(self.subject)
		return trial

	def _make_subscriber(self, email):
		subscriber = Subscribers.objects.create(
			first_name="Bounce", last_name="Tester", email=email, active=True
		)
		subscriber.subscriptions.add(self.lst)
		return subscriber


class SuppressionWiringTest(BaseSuppressionCommandTestCase):
	def test_406_deactivates_subscriber_and_writes_one_failed_notification(self):
		subscriber = self._make_subscriber("bounced@example.com")
		self._make_trial("Trial A")

		resp = _build_response(
			422, {"ErrorCode": POSTMARK_INACTIVE_RECIPIENT, "Message": "Inactive recipient"}
		)
		with patch(
			"subscriptions.management.commands.send_trials_notification.send_email",
			return_value=resp,
		):
			out = StringIO()
			call_command("send_trials_notification", stdout=out)

		subscriber.refresh_from_db()
		self.assertFalse(subscriber.active)
		self.assertFalse(
			ListSubscription.objects.get(
				subscriber=subscriber, list=self.lst
			).is_active
		)
		self.assertIsNotNone(
			ListSubscription.objects.get(
				subscriber=subscriber, list=self.lst
			).unsubscribed_at
		)
		self.assertEqual(
			FailedNotification.objects.filter(subscriber=subscriber).count(), 1
		)

	def test_second_run_does_not_attempt_to_send_to_suppressed_subscriber(self):
		subscriber = self._make_subscriber("bounced2@example.com")
		self._make_trial("Trial B")

		resp = _build_response(
			422, {"ErrorCode": POSTMARK_INACTIVE_RECIPIENT, "Message": "Inactive recipient"}
		)
		with patch(
			"subscriptions.management.commands.send_trials_notification.send_email",
			return_value=resp,
		) as mock_send:
			call_command("send_trials_notification", stdout=StringIO())
			self.assertEqual(mock_send.call_count, 1)

			call_command("send_trials_notification", stdout=StringIO())
			# No new call: the subscriber is inactive, so the command never
			# selects them again.
			self.assertEqual(mock_send.call_count, 1)

	def test_different_error_code_leaves_subscriber_active(self):
		"""
		Regression test for the send_admin_summary truthiness bug:
		`if result and result.status_code == 200` treats a falsy 422
		Response as "no response", losing the real status/error code.
		classify_postmark_response must report the true detail instead.
		"""
		subscriber = self._make_subscriber("other-error@example.com")
		self._make_trial("Trial C")

		resp = _build_response(422, {"ErrorCode": 999, "Message": "Something else"})
		with patch(
			"subscriptions.management.commands.send_admin_summary.send_email",
			return_value=resp,
		):
			admin_list = Lists.objects.create(
				list_name="Admin Bounce List",
				team=self.team,
				admin_summary=True,
				list_email_subject="Admin Summary",
			)
			admin_list.subjects.add(self.subject)
			subscriber.subscriptions.add(admin_list)

			call_command("send_admin_summary", stdout=StringIO())

		subscriber.refresh_from_db()
		self.assertTrue(subscriber.active)

		failed = FailedNotification.objects.filter(
			subscriber=subscriber, list=admin_list
		).first()
		self.assertIsNotNone(failed)
		self.assertIn("422", failed.reason)
		self.assertIn("999", failed.reason)
		self.assertNotIn("No Response", failed.reason)

	def test_connection_error_is_recorded_and_loop_continues(self):
		subscriber_a = self._make_subscriber("first@example.com")
		subscriber_b = self._make_subscriber("second@example.com")
		self._make_trial("Trial D")

		ok_result = MagicMock(status_code=200)
		ok_result.json.return_value = {"ErrorCode": 0, "Message": "OK"}

		def _side_effect(to, **kwargs):
			if to == subscriber_a.email:
				raise requests.ConnectionError("boom")
			return ok_result

		with patch(
			"subscriptions.management.commands.send_trials_notification.send_email",
			side_effect=_side_effect,
		):
			call_command("send_trials_notification", stdout=StringIO())

		self.assertTrue(
			FailedNotification.objects.filter(
				subscriber=subscriber_a, list=self.lst
			).exists()
		)
		self.assertTrue(
			SentTrialNotification.objects.filter(
				subscriber=subscriber_b, list=self.lst
			).exists()
		)
