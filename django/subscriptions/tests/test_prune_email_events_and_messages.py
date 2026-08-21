"""
Tests for prune_email_events (default 180 days) and prune_email_messages
(default 730 days) — see docs/author-outreach.md. Both commands must
never touch AuthorContactOptOut or SuppressionEvent (not introduced until
PR 2/already existing respectively); prune_email_messages must additionally
never delete an EmailMessage an AuthorOutreach (PR 3) references, tested
below against a real AuthorOutreach row now that the model exists.
"""

from datetime import timedelta
from io import StringIO

from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from gregory.models import Authors
from subscriptions.models import AuthorOutreach, AuthorOutreachCampaign, EmailEvent, EmailMessage


class PruneEmailEventsTest(TestCase):
	def _make_event(self, occurred_at, record_type=EmailEvent.RECORD_TYPE_DELIVERY):
		return EmailEvent.objects.create(
			record_type=record_type,
			message_id=f"msg-{occurred_at.isoformat()}",
			recipient="test@example.com",
			occurred_at=occurred_at,
		)

	def test_default_keeps_180_days(self):
		now = timezone.now()
		old = self._make_event(now - timedelta(days=181))
		recent = self._make_event(now - timedelta(days=10))

		out = StringIO()
		call_command("prune_email_events", stdout=out)

		self.assertFalse(EmailEvent.objects.filter(pk=old.pk).exists())
		self.assertTrue(EmailEvent.objects.filter(pk=recent.pk).exists())

	def test_custom_days_argument_is_respected(self):
		now = timezone.now()
		event = self._make_event(now - timedelta(days=40))

		out = StringIO()
		call_command("prune_email_events", "--days", "30", stdout=out)

		self.assertFalse(EmailEvent.objects.filter(pk=event.pk).exists())

	def test_dry_run_deletes_nothing(self):
		now = timezone.now()
		event = self._make_event(now - timedelta(days=200))

		out = StringIO()
		call_command("prune_email_events", "--dry-run", stdout=out)

		self.assertTrue(EmailEvent.objects.filter(pk=event.pk).exists())
		self.assertIn("DRY RUN", out.getvalue())

	def test_event_just_inside_the_window_is_kept(self):
		now = timezone.now()
		event = self._make_event(now - timedelta(days=179))

		call_command("prune_email_events", stdout=StringIO())

		self.assertTrue(EmailEvent.objects.filter(pk=event.pk).exists())


class PruneEmailMessagesTest(TestCase):
	def setUp(self):
		self.site = Site.objects.get_or_create(
			id=1, defaults={"domain": "testserver", "name": "Test Site"}
		)[0]

	def _make_message(self, sent_at, recipient="test@example.com"):
		message = EmailMessage.objects.create(
			recipient=recipient,
			tag="weekly_summary",
			message_stream="broadcast",
			site=self.site,
			accepted=True,
		)
		# sent_at is auto_now_add — override it directly after creation to
		# simulate an old row without needing to mock the clock.
		EmailMessage.objects.filter(pk=message.pk).update(sent_at=sent_at)
		message.refresh_from_db()
		return message

	def test_default_keeps_730_days(self):
		now = timezone.now()
		old = self._make_message(now - timedelta(days=731))
		recent = self._make_message(now - timedelta(days=10))

		call_command("prune_email_messages", stdout=StringIO())

		self.assertFalse(EmailMessage.objects.filter(pk=old.pk).exists())
		self.assertTrue(EmailMessage.objects.filter(pk=recent.pk).exists())

	def test_custom_days_argument_is_respected(self):
		now = timezone.now()
		message = self._make_message(now - timedelta(days=100))

		call_command("prune_email_messages", "--days", "90", stdout=StringIO())

		self.assertFalse(EmailMessage.objects.filter(pk=message.pk).exists())

	def test_dry_run_deletes_nothing(self):
		now = timezone.now()
		message = self._make_message(now - timedelta(days=800))

		out = StringIO()
		call_command("prune_email_messages", "--dry-run", stdout=out)

		self.assertTrue(EmailMessage.objects.filter(pk=message.pk).exists())
		self.assertIn("DRY RUN", out.getvalue())

	def test_author_outreach_referenced_message_is_never_pruned(self):
		"""
		AuthorOutreach (PR 3) points an email_message FK at the EmailMessage
		that carried a one-time outreach contact — the durable evidence the
		contact happened under legitimate interest (docs/author-outreach-spec.md's
		retention table: "EmailMessage ... never when referenced by an
		AuthorOutreach"). That row must survive this command regardless of
		age, while every other old EmailMessage row is pruned as normal.
		"""
		now = timezone.now()
		referenced = self._make_message(
			now - timedelta(days=1000), recipient="researcher@example.com"
		)
		unreferenced = self._make_message(now - timedelta(days=1000))

		author = Authors.objects.create(
			given_name="Ada", family_name="Researcher", ORCID="0000-0000-0000-0001"
		)
		campaign = AuthorOutreachCampaign.objects.create(
			site=self.site,
			name="Prune Test Campaign",
			utm_campaign_slug="prune-test-campaign",
		)
		AuthorOutreach.objects.create(
			campaign=campaign,
			site=self.site,
			author=author,
			email="researcher@example.com",
			status=AuthorOutreach.STATUS_SENT,
			email_message=referenced,
		)

		call_command("prune_email_messages", stdout=StringIO())

		self.assertTrue(EmailMessage.objects.filter(pk=referenced.pk).exists())
		self.assertFalse(EmailMessage.objects.filter(pk=unreferenced.pk).exists())
