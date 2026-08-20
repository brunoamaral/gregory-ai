"""
Tests for prune_email_events (default 180 days) and prune_email_messages
(default 730 days) — see AUTHOR-OUTREACH-PLAN.md PR 1. Both commands must
never touch AuthorContactOptOut or SuppressionEvent (not introduced until
PR 2/already existing respectively); prune_email_messages must additionally
never delete an EmailMessage an AuthorOutreach (PR 3) references — tested
here via the guarded import being a no-op, since AuthorOutreach does not
exist on this branch yet.
"""

from datetime import timedelta
from io import StringIO

from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from subscriptions.models import EmailEvent, EmailMessage


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

	def test_author_outreach_guard_is_a_no_op_when_model_does_not_exist(self):
		"""
		AuthorOutreach (PR 3) doesn't exist on this branch. The guarded
		import inside prune_email_messages must fail closed to "no
		exclusion" rather than raise — i.e. the command still runs
		normally and prunes old rows, proving the guard doesn't break
		pruning altogether while the model is absent.
		"""
		with self.assertRaises(ImportError):
			from subscriptions.models import AuthorOutreach  # noqa: F401

		now = timezone.now()
		old = self._make_message(now - timedelta(days=1000))

		call_command("prune_email_messages", stdout=StringIO())

		self.assertFalse(EmailMessage.objects.filter(pk=old.pk).exists())
