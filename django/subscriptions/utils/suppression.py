"""
Global opt-out helper shared by the admin "Disable all emails" action, the
send commands' Postmark-suppression handling (see subscriptions.utils.postmark),
and the Postmark Subscription Change webhook (see
subscriptions.utils.postmark_webhook).

Every path that deactivates a subscriber funnels through `deactivate_subscribers`
so they cannot drift, and each call writes a `SuppressionEvent` recording
exactly which `ListSubscription` rows it turned off. That record is what makes
`reactivate_subscribers` possible: it restores precisely what a suppression
changed, not a guess at what the subscriber "probably" still wants.
"""

import logging

from django.db import transaction
from django.utils import timezone

from subscriptions.models import ListSubscription, SuppressionEvent, Subscribers

logger = logging.getLogger(__name__)


def deactivate_subscribers(
	subscriber_ids,
	*,
	reason="",
	record_type=SuppressionEvent.RECORD_TYPE_ADMIN_MANUAL,
	suppression_reason="",
	origin="",
	message_id="",
	message_stream="",
	changed_at=None,
	raw_payload=None,
):
	"""
	Global opt-out: clear Subscribers.active and deactivate every active
	ListSubscription for the given subscribers, stamping unsubscribed_at
	where it is not already set. Writes one SuppressionEvent per subscriber
	recording the exact ListSubscription IDs it deactivated.

	Mirrors the admin "Disable all emails" action (SubscribersAdmin.make_inactive)
	exactly, so the two paths cannot drift.

	Returns (subscribers_updated, subscriptions_updated, events) — events is a
	list of the SuppressionEvent rows created, one per subscriber found.
	"""
	subscriber_ids = list(subscriber_ids)
	now = timezone.now()
	event_time = changed_at or now

	subscribers_by_id = Subscribers.objects.in_bulk(subscriber_ids)

	with transaction.atomic():
		subscribers_updated = Subscribers.objects.filter(pk__in=subscriber_ids).update(
			active=False
		)
		# Opt the selected subscribers out of every list they are still on.
		# Preserve any existing unsubscribed_at; only stamp the rows that lack one.
		stale_subs = ListSubscription.objects.filter(
			subscriber_id__in=subscriber_ids, is_active=True
		)
		affected_by_subscriber = {}
		for subscriber_id, list_subscription_id in stale_subs.values_list(
			"subscriber_id", "pk"
		):
			affected_by_subscriber.setdefault(subscriber_id, []).append(
				list_subscription_id
			)

		stale_subs.filter(unsubscribed_at__isnull=True).update(unsubscribed_at=now)
		subscriptions_updated = stale_subs.update(is_active=False)

		events = [
			SuppressionEvent.objects.create(
				subscriber=subscriber,
				email=subscriber.email,
				record_type=record_type,
				suppress_sending=True,
				suppression_reason=suppression_reason,
				origin=origin,
				message_stream=message_stream,
				message_id=message_id,
				changed_at=event_time,
				raw_payload=raw_payload,
				deactivated_list_subscription_ids=affected_by_subscriber.get(
					subscriber_id, []
				),
				action_taken=SuppressionEvent.ACTION_SUPPRESSED,
			)
			for subscriber_id, subscriber in subscribers_by_id.items()
		]

	if reason:
		logger.warning(
			"Deactivated %d subscriber(s) / %d subscription(s) (reason: %s)",
			subscribers_updated,
			subscriptions_updated,
			reason,
		)

	return subscribers_updated, subscriptions_updated, events


def reactivate_subscribers(original_event):
	"""
	Restore exactly what `original_event` deactivated: the subscriber's
	`active` flag and the specific ListSubscription rows recorded in
	`deactivated_list_subscription_ids`. Does not touch any subscription the
	subscriber opted out of independently — that is the entire point of
	recording the affected set at suppression time instead of restoring
	everything.

	Returns (subscribers_updated, subscriptions_updated).
	"""
	subscriber = original_event.subscriber
	if subscriber is None:
		return 0, 0

	with transaction.atomic():
		subscribers_updated = Subscribers.objects.filter(pk=subscriber.pk).update(
			active=True
		)
		subscriptions_updated = ListSubscription.objects.filter(
			pk__in=original_event.deactivated_list_subscription_ids or [],
			subscriber=subscriber,
		).update(is_active=True, unsubscribed_at=None)

	return subscribers_updated, subscriptions_updated
