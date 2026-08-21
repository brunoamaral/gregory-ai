"""
Admin tests for the AuthorOutreach approval queue — see
docs/author-outreach.md: Approve
selected / Skip selected (any staff with change permission), and the
superuser-only Reset for retry that deliberately reopens a slot the
eligibility/send rules had closed. Also covers the "no bulk delete, no
manual add" invariants for this queue.
"""

from django.contrib.admin.sites import site as admin_site
from django.contrib.auth.models import Permission, User
from django.contrib.sites.models import Site
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from gregory.models import Authors
from subscriptions.admin import AuthorOutreachAdmin
from subscriptions.models import AuthorOutreach, AuthorOutreachCampaign, EmailMessage

CHANGELIST_URL = reverse("admin:subscriptions_authoroutreach_changelist")


class _AuthorOutreachAdminBase(TestCase):
	@classmethod
	def setUpTestData(cls):
		cls.site = Site.objects.create(domain="outreach-admin.example.com", name="Admin")
		cls.campaign = AuthorOutreachCampaign.objects.create(
			site=cls.site, name="Admin Campaign", utm_campaign_slug="admin-campaign"
		)
		cls.superuser = User.objects.create_superuser(
			username="outreach_super", password="pw", email="super@example.com"
		)
		cls.staff = User.objects.create_user(
			username="outreach_staff",
			password="pw",
			email="staff@example.com",
			is_staff=True,
		)
		cls.staff.user_permissions.add(
			Permission.objects.get(codename="change_authoroutreach"),
			Permission.objects.get(codename="view_authoroutreach"),
		)

	def _author(self, given_name, family_name, orcid, email):
		return Authors.objects.create(
			given_name=given_name,
			family_name=family_name,
			ORCID=orcid,
			emails=[email],
			orcid_claimed=True,
			orcid_verified_email=True,
		)

	def _row(self, author, status, **kwargs):
		defaults = dict(
			campaign=self.campaign,
			site=self.site,
			author=author,
			email=author.emails[0],
			status=status,
		)
		defaults.update(kwargs)
		return AuthorOutreach.objects.create(**defaults)

	def _post_action(self, action, pks):
		return self.client.post(
			CHANGELIST_URL,
			{"action": action, "_selected_action": [str(pk) for pk in pks]},
			follow=True,
		)


class ApproveSelectedActionTest(_AuthorOutreachAdminBase):
	def setUp(self):
		self.client = Client()
		self.client.force_login(self.staff)

	def test_approves_pending_row(self):
		author = self._author("Ada", "Pending", "0000-0000-0000-0011", "ada.pending@example.com")
		row = self._row(author, AuthorOutreach.STATUS_PENDING)

		self._post_action("approve_selected", [row.pk])

		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_APPROVED)
		self.assertIsNotNone(row.approved_at)
		self.assertEqual(row.approved_by, self.staff)

	def test_ignores_non_pending_row(self):
		author = self._author("Bob", "Sent", "0000-0000-0000-0012", "bob.sent@example.com")
		row = self._row(author, AuthorOutreach.STATUS_SENT)

		self._post_action("approve_selected", [row.pk])

		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_SENT)
		self.assertIsNone(row.approved_by)


class SkipSelectedActionTest(_AuthorOutreachAdminBase):
	def setUp(self):
		self.client = Client()
		self.client.force_login(self.staff)

	def test_skips_pending_row(self):
		author = self._author("Cid", "Pending", "0000-0000-0000-0013", "cid.pending@example.com")
		row = self._row(author, AuthorOutreach.STATUS_PENDING)

		self._post_action("skip_selected", [row.pk])

		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_SKIPPED)

	def test_skips_approved_row(self):
		author = self._author("Dee", "Approved", "0000-0000-0000-0014", "dee.approved@example.com")
		row = self._row(author, AuthorOutreach.STATUS_APPROVED)

		self._post_action("skip_selected", [row.pk])

		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_SKIPPED)

	def test_ignores_sent_row(self):
		author = self._author("Eve", "Sent", "0000-0000-0000-0015", "eve.sent@example.com")
		row = self._row(author, AuthorOutreach.STATUS_SENT)

		self._post_action("skip_selected", [row.pk])

		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_SENT)


class ResetForRetryActionTest(_AuthorOutreachAdminBase):
	def test_superuser_reopens_failed_slot(self):
		self.client = Client()
		self.client.force_login(self.superuser)
		author = self._author("Fay", "Failed", "0000-0000-0000-0016", "fay.failed@example.com")
		row = self._row(
			author,
			AuthorOutreach.STATUS_FAILED,
			approved_at=timezone.now(),
			approved_by=self.superuser,
			sent_at=timezone.now(),
			error_message="Postmark 500",
		)

		self._post_action("reset_for_retry", [row.pk])

		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_PENDING)
		self.assertIsNone(row.approved_at)
		self.assertIsNone(row.approved_by)
		self.assertIsNone(row.sent_at)
		self.assertEqual(row.error_message, "")

	def test_superuser_ignores_pending_row(self):
		self.client = Client()
		self.client.force_login(self.superuser)
		author = self._author("Gus", "Pending", "0000-0000-0000-0017", "gus.pending@example.com")
		row = self._row(author, AuthorOutreach.STATUS_PENDING)

		self._post_action("reset_for_retry", [row.pk])

		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_PENDING)

	def test_non_superuser_cannot_reach_the_action(self):
		"""
		reset_for_retry is stripped out of get_actions() for anyone who
		isn't a superuser, so posting it as a non-superuser must be a
		no-op — the failed row stays failed, the slot stays burned.
		"""
		self.client = Client()
		self.client.force_login(self.staff)
		author = self._author("Hal", "Failed", "0000-0000-0000-0018", "hal.failed@example.com")
		row = self._row(author, AuthorOutreach.STATUS_FAILED, error_message="boom")

		self._post_action("reset_for_retry", [row.pk])

		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_FAILED)
		self.assertEqual(row.error_message, "boom")

	def test_superuser_reopens_sending_row_with_no_email_message_evidence(self):
		"""
		A row stuck in 'sending' with no EmailMessage row for it at all is
		the common, safe-to-retry case: the process most likely crashed
		*before* ever contacting Postmark, so there's no evidence a message
		went out. It must be reopened just like failed/skipped/cancelled.
		"""
		self.client = Client()
		self.client.force_login(self.superuser)
		author = self._author("Ivy", "Sending", "0000-0000-0000-0019", "ivy.sending@example.com")
		row = self._row(author, AuthorOutreach.STATUS_SENDING)

		self._post_action("reset_for_retry", [row.pk])

		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_PENDING)

	def test_superuser_refuses_to_reopen_sending_row_with_email_message_evidence(self):
		"""
		A row stuck in 'sending' with a matching EmailMessage row already
		recorded (site, recipient, tag='author_outreach') is positive
		evidence the message may have already reached Postmark — reopening
		it risks a genuine second send to the same person, which the spec's
		"one email per author per site, ever" rule forbids. The action must
		refuse and leave the row exactly as it was.
		"""
		self.client = Client()
		self.client.force_login(self.superuser)
		author = self._author("Jax", "Sending", "0000-0000-0000-0020", "jax.sending@example.com")
		row = self._row(author, AuthorOutreach.STATUS_SENDING)
		EmailMessage.objects.create(
			recipient=row.email,
			site=self.site,
			tag="author_outreach",
			accepted=True,
		)

		self._post_action("reset_for_retry", [row.pk])

		row.refresh_from_db()
		self.assertEqual(row.status, AuthorOutreach.STATUS_SENDING)

	def test_get_actions_excludes_reset_for_retry_for_non_superuser(self):
		factory = RequestFactory()
		request = factory.get(CHANGELIST_URL)
		request.user = self.staff
		admin = AuthorOutreachAdmin(AuthorOutreach, admin_site)
		self.assertNotIn("reset_for_retry", admin.get_actions(request))

	def test_get_actions_includes_reset_for_retry_for_superuser(self):
		factory = RequestFactory()
		request = factory.get(CHANGELIST_URL)
		request.user = self.superuser
		admin = AuthorOutreachAdmin(AuthorOutreach, admin_site)
		self.assertIn("reset_for_retry", admin.get_actions(request))


class NoBulkDeleteOrManualAddTest(_AuthorOutreachAdminBase):
	"""
	docs/author-outreach-spec.md's retention table marks AuthorOutreach
	"Indefinite" — the record has to outlive everything else. Deletion is
	disabled outright (not just the bulk action), and rows can only be
	created by build_author_outreach (PR 4), never by hand in the admin.
	"""

	def test_has_delete_permission_is_false(self):
		factory = RequestFactory()
		request = factory.get(CHANGELIST_URL)
		request.user = self.superuser
		admin = AuthorOutreachAdmin(AuthorOutreach, admin_site)
		self.assertFalse(admin.has_delete_permission(request))

	def test_delete_selected_is_not_an_available_action(self):
		factory = RequestFactory()
		request = factory.get(CHANGELIST_URL)
		request.user = self.superuser
		admin = AuthorOutreachAdmin(AuthorOutreach, admin_site)
		self.assertNotIn("delete_selected", admin.get_actions(request))

	def test_has_add_permission_is_false(self):
		factory = RequestFactory()
		request = factory.get(CHANGELIST_URL)
		request.user = self.superuser
		admin = AuthorOutreachAdmin(AuthorOutreach, admin_site)
		self.assertFalse(admin.has_add_permission(request))
