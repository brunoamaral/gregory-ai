from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.test import TestCase, Client
from django.urls import reverse

from organizations.models import Organization
from gregory.models import Team
from subscriptions.models import (
	Announcement,
	AnnouncementRecipient,
	Lists,
	ListSubscription,
	Subscribers,
)


class AnnouncementConfirmCountTest(TestCase):
	"""The GET confirm page must report how many subscribers will actually
	be mailed (post-skip), not the raw list audience — send_announcement()
	skips anyone with a successful AnnouncementRecipient row already, so the
	button and the operator-facing count need to agree with that."""

	@classmethod
	def setUpTestData(cls):
		cls.superuser = User.objects.create_superuser(
			username="confirm_admin",
			password="password",
			email="confirm_admin@example.com",
		)
		cls.org = Organization.objects.create(name="Confirm Org")
		cls.team = Team.objects.create(
			organization=cls.org, name="Confirm Team", slug="confirm-team"
		)
		cls.site = Site.objects.get_or_create(
			id=30, defaults={"domain": "confirm.example.com", "name": "Confirm"}
		)[0]
		cls.lst = Lists.objects.create(
			list_name="Confirm List", team=cls.team, site=cls.site
		)

	def setUp(self):
		self.client = Client()
		self.client.force_login(self.superuser)

	def _confirm_url(self, pk):
		return reverse("admin:subscriptions_announcement_send", args=[pk])

	def _make_subscriber(self, email):
		sub = Subscribers.objects.create(
			first_name="Sub", last_name=email, email=email, active=True
		)
		ListSubscription.objects.create(subscriber=sub, list=self.lst, is_active=True)
		return sub

	def _make_announcement(self):
		ann = Announcement.objects.create(
			subject="Confirm Count Test",
			body="<p>Body</p>",
			status="failed",
			organization=self.org,
		)
		ann.lists.add(self.lst)
		return ann

	def test_no_prior_recipients_reports_full_audience(self):
		self._make_subscriber("new1@example.com")
		self._make_subscriber("new2@example.com")
		ann = self._make_announcement()

		response = self.client.get(self._confirm_url(ann.pk))

		self.assertEqual(response.context["total_subscribers"], 2)
		self.assertEqual(response.context["new_recipients_count"], 2)
		self.assertEqual(response.context["already_received_count"], 0)

	def test_prior_successful_recipients_are_excluded_from_new_count(self):
		already_sent = self._make_subscriber("already@example.com")
		self._make_subscriber("pending@example.com")
		ann = self._make_announcement()
		AnnouncementRecipient.objects.create(
			announcement=ann,
			subscriber=already_sent,
			list=self.lst,
			success=True,
		)

		response = self.client.get(self._confirm_url(ann.pk))

		self.assertEqual(response.context["total_subscribers"], 2)
		self.assertEqual(response.context["new_recipients_count"], 1)
		self.assertEqual(response.context["already_received_count"], 1)
		self.assertContains(response, "Queue Send to 1 Subscriber")

	def test_failed_prior_attempt_still_counts_as_new(self):
		"""A non-successful AnnouncementRecipient row (a genuine delivery
		failure, not a skip) must NOT be treated as already-received —
		only success=True rows represent a subscriber who was actually
		mailed."""
		retry_target = self._make_subscriber("retry@example.com")
		ann = self._make_announcement()
		AnnouncementRecipient.objects.create(
			announcement=ann,
			subscriber=retry_target,
			list=self.lst,
			success=False,
			error_message="Connection error: timeout",
		)

		response = self.client.get(self._confirm_url(ann.pk))

		self.assertEqual(response.context["new_recipients_count"], 1)
		self.assertEqual(response.context["already_received_count"], 0)
