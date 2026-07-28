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
