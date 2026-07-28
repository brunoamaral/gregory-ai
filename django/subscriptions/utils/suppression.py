"""
Global opt-out helper shared by the admin "Disable all emails" action and the
send commands' Postmark-suppression handling (see subscriptions.utils.postmark).
"""

import logging

from django.db import transaction
from django.utils import timezone

from subscriptions.models import ListSubscription, Subscribers

logger = logging.getLogger(__name__)


def deactivate_subscribers(subscriber_ids, *, reason=""):
	"""
	Global opt-out: clear Subscribers.active and deactivate every active
	ListSubscription for the given subscribers, stamping unsubscribed_at
	where it is not already set.

	Mirrors the admin "Disable all emails" action (SubscribersAdmin.make_inactive)
	exactly, so the two paths cannot drift.

	Returns (subscribers_updated, subscriptions_updated).
	"""
	subscriber_ids = list(subscriber_ids)
	now = timezone.now()

	with transaction.atomic():
		subscribers_updated = Subscribers.objects.filter(pk__in=subscriber_ids).update(
			active=False
		)
		# Opt the selected subscribers out of every list they are still on.
		# Preserve any existing unsubscribed_at; only stamp the rows that lack one.
		stale_subs = ListSubscription.objects.filter(
			subscriber_id__in=subscriber_ids, is_active=True
		)
		stale_subs.filter(unsubscribed_at__isnull=True).update(unsubscribed_at=now)
		subscriptions_updated = stale_subs.update(is_active=False)

	if reason:
		logger.warning(
			"Deactivated %d subscriber(s) / %d subscription(s) (reason: %s)",
			subscribers_updated,
			subscriptions_updated,
			reason,
		)

	return subscribers_updated, subscriptions_updated
