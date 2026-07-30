"""
Processing for Postmark's "Subscription Change" webhook event — the
suppression/reactivation signal that subscriptions.views.postmark_webhook
dispatches into. See docs/subscriptions.md for the reactivation policy this
implements and why it exists (the short version: reactivation only works if
suppression first recorded exactly what it changed — see
subscriptions.utils.suppression).

Deliberately NOT implemented here, and why:

- Signature verification: Postmark does not support HMAC webhook signatures,
  despite a TypeScript sample in their docs suggesting otherwise. Basic auth
  (subscriptions.views) is the only supported protection.
- Dedup on Postmark's MessageID alone: Subscription Change events carry an
  all-zero placeholder MessageID when the change has no originating message
  (e.g. a manual suppression in the Postmark UI). The idempotency key here is
  (record_type, email, changed_at) instead — see SuppressionEvent.Meta.
- Gating reactivation on the Origin field: undocumented semantics on an
  unsuppress event (see docs/subscriptions.md). Recorded as data only.
"""

import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from subscriptions.models import Subscribers, SuppressionEvent
from subscriptions.utils.suppression import (
	deactivate_subscribers,
	reactivate_subscribers,
)

logger = logging.getLogger(__name__)

# Consent decays. An unsuppress arriving long after the original suppression
# would restore subscriptions nobody has confirmed in a long time. A named
# module constant rather than a Django setting: this is a policy decision,
# not a per-deployment knob, and a settings key invites it being changed
# without the reasoning behind it being revisited.
REACTIVATION_MAX_AGE = timedelta(days=365)

# Reactivation policy (docs/subscriptions.md#suppression-and-reactivation-webhook):
# HardBounce and ManualSuppression auto-restore (subject to the staleness cap
# above); SpamComplaint never does (a recorded objection outranks a later
# unsuppress); anything else — missing reason, an unrecognised value, no
# matching original SuppressionEvent — is recorded and flagged for review.
AUTO_RESTORE_REASONS = frozenset({"HardBounce", "ManualSuppression"})
STICKY_REASONS = frozenset({"SpamComplaint"})

# send_email always sends on this stream (see
# subscriptions/management/commands/utils/send_email.py); there are no
# transactional streams configured yet, so this is a sanity check to record
# and assert, not a routing decision (docs/subscriptions.md).
EXPECTED_MESSAGE_STREAM = "broadcast"


def _parse_changed_at(raw):
	"""Parse Postmark's ChangedAt; fall back to now() if missing/unparseable."""
	if raw:
		parsed = parse_datetime(raw)
		if parsed is not None:
			if timezone.is_naive(parsed):
				parsed = timezone.make_aware(parsed, timezone.utc)
			return parsed
		logger.warning(
			"postmark_webhook: could not parse ChangedAt %r; using now().", raw
		)
	return timezone.now()


def handle_subscription_change(payload):
	"""
	Process one Postmark "Subscription Change" event payload.

	Returns the SuppressionEvent row created for this event, or None when the
	event was a no-op: a replay of an already-recorded (record_type, email,
	changed_at), or older than the most recently applied event for that
	recipient.
	"""
	recipient = (payload.get("Recipient") or "").strip().lower()
	if not recipient:
		logger.warning(
			"postmark_webhook: SubscriptionChange payload missing Recipient; ignoring. %s",
			payload,
		)
		return None

	suppress_sending = bool(payload.get("SuppressSending"))
	suppression_reason = payload.get("SuppressionReason") or ""
	origin = payload.get("Origin") or ""
	message_stream = payload.get("MessageStream") or ""
	message_id = payload.get("MessageID") or ""
	changed_at = _parse_changed_at(payload.get("ChangedAt"))

	# Ordering: events can arrive out of order. Compare against the most
	# recent ChangedAt already recorded for this recipient (by event time,
	# not insertion order) and ignore anything older, so a stale suppress
	# arriving after a newer unsuppress (or vice versa) can't win.
	latest = (
		SuppressionEvent.objects.filter(email=recipient).order_by("-changed_at").first()
	)
	if latest is not None and changed_at < latest.changed_at:
		logger.info(
			"postmark_webhook: ignoring out-of-order SubscriptionChange for %s "
			"(changed_at %s < latest applied %s).",
			recipient,
			changed_at,
			latest.changed_at,
		)
		try:
			SuppressionEvent.objects.create(
				subscriber=latest.subscriber,
				email=recipient,
				record_type=SuppressionEvent.RECORD_TYPE_SUBSCRIPTION_CHANGE,
				suppress_sending=suppress_sending,
				suppression_reason=suppression_reason,
				origin=origin,
				message_stream=message_stream,
				message_id=message_id,
				changed_at=changed_at,
				raw_payload=payload,
				action_taken=SuppressionEvent.ACTION_RECORD_ONLY,
				flag_reason="Out-of-order event: older than the most recently "
				"applied event for this recipient.",
			)
		except IntegrityError:
			pass  # exact replay of an already-recorded out-of-order event
		return None

	subscriber = Subscribers.objects.filter(email=recipient).first()

	# send_email only ever sends on the "broadcast" stream today (see
	# subscriptions/management/commands/utils/send_email.py); there are no
	# transactional streams in use. An event on any other stream may reflect
	# something we don't understand yet, so record it without acting on it
	# rather than assume it means the same thing.
	if message_stream and message_stream != EXPECTED_MESSAGE_STREAM:
		logger.warning(
			"postmark_webhook: SubscriptionChange for %s on unexpected "
			"MessageStream %r (expected %r); recording only.",
			recipient,
			message_stream,
			EXPECTED_MESSAGE_STREAM,
		)
		try:
			return SuppressionEvent.objects.create(
				subscriber=subscriber,
				email=recipient,
				record_type=SuppressionEvent.RECORD_TYPE_SUBSCRIPTION_CHANGE,
				suppress_sending=suppress_sending,
				suppression_reason=suppression_reason,
				origin=origin,
				message_stream=message_stream,
				message_id=message_id,
				changed_at=changed_at,
				raw_payload=payload,
				action_taken=SuppressionEvent.ACTION_RECORD_ONLY,
				flag_reason=f"MessageStream {message_stream!r} is not "
				f"{EXPECTED_MESSAGE_STREAM!r} — recorded but not acted on.",
			)
		except IntegrityError:
			return None  # replay

	if suppress_sending:
		return _handle_suppress(
			subscriber,
			recipient,
			suppression_reason,
			origin,
			message_stream,
			message_id,
			changed_at,
			payload,
		)
	return _handle_unsuppress(
		subscriber,
		recipient,
		suppression_reason,
		origin,
		message_stream,
		message_id,
		changed_at,
		payload,
	)


def _handle_suppress(
	subscriber,
	recipient,
	suppression_reason,
	origin,
	message_stream,
	message_id,
	changed_at,
	payload,
):
	if subscriber is None:
		# Unknown recipient: a test send, an old address, a different system
		# on the same server. Record the event; don't error, don't create one.
		try:
			return SuppressionEvent.objects.create(
				subscriber=None,
				email=recipient,
				record_type=SuppressionEvent.RECORD_TYPE_SUBSCRIPTION_CHANGE,
				suppress_sending=True,
				suppression_reason=suppression_reason,
				origin=origin,
				message_stream=message_stream,
				message_id=message_id,
				changed_at=changed_at,
				raw_payload=payload,
				action_taken=SuppressionEvent.ACTION_RECORD_ONLY,
				flag_reason="Unknown recipient — no matching Subscribers row.",
			)
		except IntegrityError:
			return None  # replay

	try:
		with transaction.atomic():
			_subscribers_updated, _subscriptions_updated, events = (
				deactivate_subscribers(
					[subscriber.pk],
					reason=f"Postmark suppression ({suppression_reason or 'unknown reason'})",
					record_type=SuppressionEvent.RECORD_TYPE_SUBSCRIPTION_CHANGE,
					suppression_reason=suppression_reason,
					origin=origin,
					message_id=message_id,
					message_stream=message_stream,
					changed_at=changed_at,
					raw_payload=payload,
				)
			)
	except IntegrityError:
		return None  # replay

	return events[0] if events else None


def _handle_unsuppress(
	subscriber,
	recipient,
	suppression_reason,
	origin,
	message_stream,
	message_id,
	changed_at,
	payload,
):
	# The original suppression this unsuppress might reverse: the most recent
	# actually-applied suppression for this recipient. Record-only suppression
	# events (unknown recipient, out-of-order) don't count — nothing was
	# actually deactivated, so there's nothing to restore.
	original = (
		SuppressionEvent.objects.filter(
			email=recipient,
			suppress_sending=True,
			action_taken=SuppressionEvent.ACTION_SUPPRESSED,
		)
		.order_by("-changed_at")
		.first()
	)

	action_taken = SuppressionEvent.ACTION_RECORD_ONLY
	flag_reason = ""

	if suppression_reason in STICKY_REASONS:
		flag_reason = "Spam complaint is sticky — never auto-restored, even by a later unsuppress."
	elif original is None:
		flag_reason = (
			"No matching SuppressionEvent found for this recipient — "
			"can't verify original suppression reason or age, so not restored."
		)
	elif suppression_reason not in AUTO_RESTORE_REASONS:
		flag_reason = (
			f"Unrecognised or missing SuppressionReason "
			f"({suppression_reason or 'none'}) on the unsuppress event — not auto-restored."
		)
	elif (changed_at - original.changed_at) > REACTIVATION_MAX_AGE:
		flag_reason = (
			f"Original suppression ({original.changed_at.isoformat()}) is older "
			f"than the {REACTIVATION_MAX_AGE.days}-day reactivation window — not restored."
		)
	else:
		action_taken = SuppressionEvent.ACTION_AUTO_RESTORED

	try:
		with transaction.atomic():
			event = SuppressionEvent.objects.create(
				subscriber=subscriber or (original.subscriber if original else None),
				email=recipient,
				record_type=SuppressionEvent.RECORD_TYPE_SUBSCRIPTION_CHANGE,
				suppress_sending=False,
				suppression_reason=suppression_reason,
				origin=origin,
				message_stream=message_stream,
				message_id=message_id,
				changed_at=changed_at,
				raw_payload=payload,
				action_taken=action_taken,
				flag_reason=flag_reason,
			)
			if action_taken == SuppressionEvent.ACTION_AUTO_RESTORED:
				reactivate_subscribers(original)
	except IntegrityError:
		return None  # replay

	return event
