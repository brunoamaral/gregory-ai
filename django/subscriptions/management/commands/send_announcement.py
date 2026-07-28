import logging

from django.core.management.base import BaseCommand

from subscriptions.models import Announcement
from subscriptions.utils.announcement_send import send_announcement

logger = logging.getLogger(__name__)


class Command(BaseCommand):
	help = (
		"Sends announcements that have been queued from the admin 'Send to "
		"Subscribers' action (status='queued'). Intended to run from cron — "
		"the admin action only validates and enqueues so a large send can "
		"never be killed mid-flight by a request timeout. Safe to run "
		"again on an announcement stuck in 'sending' from a previous crash: "
		"send_announcement() skips any subscriber already recorded as a "
		"successful AnnouncementRecipient."
	)

	def handle(self, *args, **options):
		queued = Announcement.objects.filter(status="queued")

		if not queued.exists():
			self.stdout.write(self.style.WARNING("No queued announcements found."))
			return

		for announcement in queued:
			# Compare-and-swap claim: this UPDATE only affects the row if it
			# is still 'queued'. Two overlapping cron runs (or a manual
			# invocation racing the schedule) can both load the same row
			# from the queryset above, but only one of them will find
			# claimed == 1 here — the loser skips it instead of sending a
			# duplicate email before send_announcement() gets a chance to
			# flip the status itself.
			claimed = Announcement.objects.filter(
				pk=announcement.pk, status="queued"
			).update(status="sending")
			if not claimed:
				self.stdout.write(
					self.style.WARNING(
						f"Skipping announcement '{announcement.subject}' "
						f"(id={announcement.pk}) — already claimed by another run."
					)
				)
				continue

			self.stdout.write(
				self.style.SUCCESS(
					f"Sending announcement '{announcement.subject}' (id={announcement.pk})…"
				)
			)
			summary = send_announcement(announcement)
			self.stdout.write(
				self.style.SUCCESS(
					f"  sent={summary['sent']} suppressed={summary['suppressed']} "
					f"failed={summary['failed']} skipped={summary['skipped']} "
					f"-> status={announcement.status}"
				)
			)
