import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gregory.tests.test_settings')
django.setup()

from django.test import TestCase
from organizations.models import Organization
from gregory.models import Team
from subscriptions.models import Lists, Subscribers, FailedNotification
from subscriptions.management.commands.utils.handle_postmark_errors import (
	handle_postmark_error,
	deactivate_subscriber_from_list,
	parse_postmark_error,
	DEACTIVATION_ERROR_CODES,
)


class MockResponse:
	"""Mock requests.Response for Postmark API responses."""

	def __init__(self, status_code, json_data=None):
		self.status_code = status_code
		self._json_data = json_data

	def json(self):
		if self._json_data is None:
			raise ValueError("No JSON data")
		return self._json_data


class ParsePostmarkErrorTest(TestCase):
	def test_success_returns_none(self):
		result = MockResponse(200, {"ErrorCode": 0, "Message": "OK"})
		self.assertIsNone(parse_postmark_error(result))

	def test_200_with_error_code(self):
		result = MockResponse(200, {"ErrorCode": 406, "Message": "Inactive recipient"})
		error_code, error_message, details = parse_postmark_error(result)
		self.assertEqual(error_code, 406)
		self.assertIn("Inactive recipient", details)

	def test_422_response(self):
		result = MockResponse(422, {"ErrorCode": 406, "Message": "Inactive recipient"})
		error_code, error_message, details = parse_postmark_error(result)
		self.assertEqual(error_code, 406)
		self.assertIn("422", details)

	def test_none_response(self):
		error_code, error_message, details = parse_postmark_error(None)
		self.assertIn("No response", details)

	def test_non_422_non_200_response(self):
		result = MockResponse(500)
		error_code, error_message, details = parse_postmark_error(result)
		self.assertIsNone(error_code)
		self.assertIn("500", details)


class DeactivateSubscriberFromListTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name='Test Org')
		self.team = Team.objects.create(organization=self.org, name='Alpha', slug='alpha')
		self.list1 = Lists.objects.create(list_name='List 1', team=self.team)
		self.list2 = Lists.objects.create(list_name='List 2', team=self.team)
		self.subscriber = Subscribers.objects.create(
			first_name='Jane', last_name='Doe', email='jane@example.com'
		)
		self.subscriber.subscriptions.add(self.list1, self.list2)

	def test_removes_from_specific_list(self):
		deactivate_subscriber_from_list(self.subscriber, self.list1, 406, "Inactive recipient")
		self.assertNotIn(self.list1, self.subscriber.subscriptions.all())
		self.assertIn(self.list2, self.subscriber.subscriptions.all())
		self.subscriber.refresh_from_db()
		self.assertTrue(self.subscriber.active)

	def test_deactivates_when_no_subscriptions_remain(self):
		self.subscriber.subscriptions.remove(self.list2)  # Only list1 left
		deactivate_subscriber_from_list(self.subscriber, self.list1, 406, "Inactive recipient")
		self.subscriber.refresh_from_db()
		self.assertFalse(self.subscriber.active)
		self.assertEqual(self.subscriber.subscriptions.count(), 0)

	def test_history_records_change_reason(self):
		deactivate_subscriber_from_list(self.subscriber, self.list1, 406, "Inactive recipient")
		history = self.subscriber.history.first()
		self.assertIn("Auto-removed", history.history_change_reason)
		self.assertIn("406", history.history_change_reason)

	def test_global_deactivation_history_records_reason(self):
		self.subscriber.subscriptions.remove(self.list2)  # Only list1 left
		deactivate_subscriber_from_list(self.subscriber, self.list1, 406, "Inactive recipient")
		history = self.subscriber.history.first()
		self.assertIn("Auto-deactivated", history.history_change_reason)
		self.assertIn("no remaining subscriptions", history.history_change_reason)


class HandlePostmarkErrorTest(TestCase):
	def setUp(self):
		self.org = Organization.objects.create(name='Test Org')
		self.team = Team.objects.create(organization=self.org, name='Beta', slug='beta')
		self.lst = Lists.objects.create(list_name='Trials', team=self.team)
		self.subscriber = Subscribers.objects.create(
			first_name='Test', last_name='User', email='test@example.com'
		)
		self.subscriber.subscriptions.add(self.lst)

	def test_success_returns_none(self):
		result = MockResponse(200, {"ErrorCode": 0, "Message": "OK"})
		self.assertIsNone(handle_postmark_error(result, self.subscriber, self.lst))

	def test_success_does_not_create_failed_notification(self):
		result = MockResponse(200, {"ErrorCode": 0, "Message": "OK"})
		handle_postmark_error(result, self.subscriber, self.lst)
		self.assertEqual(FailedNotification.objects.count(), 0)

	def test_406_creates_failed_notification_and_deactivates(self):
		result = MockResponse(422, {"ErrorCode": 406, "Message": "Inactive recipient"})
		error_result = handle_postmark_error(result, self.subscriber, self.lst)

		self.assertTrue(error_result['was_error'])
		self.assertTrue(error_result['was_deactivated'])
		self.assertEqual(FailedNotification.objects.filter(subscriber=self.subscriber).count(), 1)
		self.assertNotIn(self.lst, self.subscriber.subscriptions.all())

	def test_non_deactivation_error_does_not_remove_from_list(self):
		result = MockResponse(200, {"ErrorCode": 999, "Message": "Some other error"})
		error_result = handle_postmark_error(result, self.subscriber, self.lst)

		self.assertTrue(error_result['was_error'])
		self.assertFalse(error_result['was_deactivated'])
		self.assertIn(self.lst, self.subscriber.subscriptions.all())
		self.assertEqual(FailedNotification.objects.filter(subscriber=self.subscriber).count(), 1)

	def test_406_with_last_subscription_deactivates_globally(self):
		result = MockResponse(422, {"ErrorCode": 406, "Message": "Inactive recipient"})
		handle_postmark_error(result, self.subscriber, self.lst)

		self.subscriber.refresh_from_db()
		self.assertFalse(self.subscriber.active)

	def test_406_with_multiple_subscriptions_stays_active(self):
		list2 = Lists.objects.create(list_name='Other List', team=self.team)
		self.subscriber.subscriptions.add(list2)

		result = MockResponse(422, {"ErrorCode": 406, "Message": "Inactive recipient"})
		handle_postmark_error(result, self.subscriber, self.lst)

		self.subscriber.refresh_from_db()
		self.assertTrue(self.subscriber.active)
		self.assertNotIn(self.lst, self.subscriber.subscriptions.all())
		self.assertIn(list2, self.subscriber.subscriptions.all())

	def test_generic_500_error_creates_failed_notification(self):
		result = MockResponse(500)
		error_result = handle_postmark_error(result, self.subscriber, self.lst)

		self.assertTrue(error_result['was_error'])
		self.assertFalse(error_result['was_deactivated'])
		self.assertEqual(FailedNotification.objects.count(), 1)
		self.assertIn(self.lst, self.subscriber.subscriptions.all())
