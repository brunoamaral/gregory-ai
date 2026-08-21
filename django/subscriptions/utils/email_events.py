"""
Processing for the Postmark webhook events that feed EmailEvent: Delivery,
Bounce, SpamComplaint, Open, Click.

subscriptions.views.postmark_webhook dispatches those five RecordTypes here.
SubscriptionChange is deliberately NOT among them — it goes only to
subscriptions.utils.postmark_webhook.handle_subscription_change, which
writes SuppressionEvent, the sole record for that type. The two were
previously dual-written (this module additionally recording a
SubscriptionChange EmailEvent), which both duplicated the record in the
admin and collided: SubscriptionChange carries an all-zero placeholder
MessageID when it has no originating message, and without `recipient` in
EmailEvent's dedup key, two different people suppressed in the same second
would violate the unique constraint — the second silently discarded below as
a "replay" it wasn't. See EmailEvent's model docstring for the full
reasoning. `handle_email_event` needs no special case for this: RecordType
SubscriptionChange simply isn't in EmailEvent.RECORD_TYPES any more, so the
early-return guard below already turns it into a no-op.

This module never touches Subscribers, ListSubscription, or
SuppressionEvent; it only appends to the EmailEvent log and updates the
aggregate outcome fields on the matching EmailMessage, if any.

See EmailEvent's model docstring (subscriptions/models.py) for exactly what
is — and, more importantly, is deliberately NOT — stored from each payload.

Also writes AuthorContactOptOut (AUTHOR-OUTREACH-PLAN.md "PR 2 — Author
do-not-contact"): a hard bounce or a spam complaint on *any* message this
system sends — not only outreach — means the address must never be used
again anywhere, via subscriptions.utils.author_optout.record_author_opt_out,
which is itself failure-tolerant. See that module's docstring for why every
caller, this one included, needs the write to be unable to turn a success
into a failure.
"""

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from subscriptions.models import AuthorContactOptOut, EmailEvent, EmailMessage
from subscriptions.utils.author_optout import record_author_opt_out

logger = logging.getLogger(__name__)

# Postmark Bounce "Type" values that mean the address itself is bad and
# should never be used again, per AUTHOR-OUTREACH-SPEC.md "Bounce and
# complaint handling" ("Hard bounce / bad address"). BadEmailAddress is
# distinct from HardBounce in Postmark's own taxonomy (malformed/rejected
# address vs. a mailbox that no longer exists) but both mean the same thing
# for this purpose.
HARD_BOUNCE_TYPES = frozenset({"HardBounce", "BadEmailAddress"})

# Which payload field carries this event's own timestamp, per RecordType.
# Postmark names this field differently per event: DeliveredAt for
# Delivery, BouncedAt for Bounce/SpamComplaint, ReceivedAt for Open/Click.
# (SubscriptionChange uses ChangedAt, per
# subscriptions.utils.postmark_webhook._parse_changed_at — irrelevant here
# since this module never records that RecordType; see the module
# docstring.)
_OCCURRED_AT_FIELD = {
	EmailEvent.RECORD_TYPE_DELIVERY: "DeliveredAt",
	EmailEvent.RECORD_TYPE_BOUNCE: "BouncedAt",
	EmailEvent.RECORD_TYPE_SPAM_COMPLAINT: "BouncedAt",
	EmailEvent.RECORD_TYPE_OPEN: "ReceivedAt",
	EmailEvent.RECORD_TYPE_CLICK: "ReceivedAt",
}

# Which payload field carries the recipient address, per RecordType. Every
# event uses "Recipient" *except* Bounce and SpamComplaint, which — per
# Postmark's own webhook docs (developer.postmarkapp.com/developer/webhooks/
# bounce-webhook, .../spam-complaint-webhook) — use "Email" instead. Easy to
# get wrong (and get silently zero recipients on every bounce) because nothing
# about the field's absence raises; verified against Postmark's documented
# example payloads rather than assumed.
_RECIPIENT_FIELD = {
	EmailEvent.RECORD_TYPE_DELIVERY: "Recipient",
	EmailEvent.RECORD_TYPE_BOUNCE: "Email",
	EmailEvent.RECORD_TYPE_SPAM_COMPLAINT: "Email",
	EmailEvent.RECORD_TYPE_OPEN: "Recipient",
	EmailEvent.RECORD_TYPE_CLICK: "Recipient",
}


def _parse_occurred_at(record_type, payload):
	"""Parse this event's own timestamp; fall back to now() if missing/unparseable."""
	field = _OCCURRED_AT_FIELD.get(record_type)
	raw = payload.get(field) if field else None
	if raw:
		parsed = parse_datetime(raw)
		if parsed is not None:
			if timezone.is_naive(parsed):
				parsed = timezone.make_aware(parsed, timezone.utc)
			return parsed
		logger.warning(
			"email_events: could not parse %s=%r for %s event; using now().",
			field,
			raw,
			record_type,
		)
	return timezone.now()


def _find_message(payload):
	"""
	Correlate this event to the EmailMessage it belongs to.

	Correlation order: Metadata.msg_token first (the opaque UUID a sender
	can choose to echo through send_email's `metadata` param), then
	Postmark's own MessageID. Neither may match anything — the four
	existing senders don't pass metadata yet, and a message sent before
	this feature shipped has no EmailMessage row at all — in which case the
	event is still recorded, with message=None.
	"""
	metadata = payload.get("Metadata")
	msg_token = metadata.get("msg_token") if isinstance(metadata, dict) else None
	if msg_token:
		message = EmailMessage.objects.filter(msg_token=msg_token).first()
		if message is not None:
			return message

	message_id = payload.get("MessageID") or ""
	if message_id:
		return EmailMessage.objects.filter(message_id=message_id).first()

	return None


def _update_message_aggregates(message, record_type, occurred_at, extra):
	"""Update the one or two aggregate outcome fields this event kind owns."""
	update_fields = []

	if record_type == EmailEvent.RECORD_TYPE_DELIVERY:
		message.delivered_at = occurred_at
		update_fields.append("delivered_at")
	elif record_type == EmailEvent.RECORD_TYPE_OPEN:
		# First open only — Postmark is configured that way, and this
		# mirrors it: later opens still get their own EmailEvent row, but
		# don't move this field.
		if message.first_opened_at is None:
			message.first_opened_at = occurred_at
			update_fields.append("first_opened_at")
	elif record_type == EmailEvent.RECORD_TYPE_BOUNCE:
		message.bounced_at = occurred_at
		message.bounce_type = extra.get("bounce_type", "")
		update_fields.extend(["bounced_at", "bounce_type"])
	elif record_type == EmailEvent.RECORD_TYPE_SPAM_COMPLAINT:
		message.complained_at = occurred_at
		update_fields.append("complained_at")
	# Click owns no aggregate field on EmailMessage.

	if update_fields:
		message.save(update_fields=update_fields)


def handle_email_event(payload):
	"""
	Process one Postmark webhook payload and write one EmailEvent row.

	Accepts RecordType Delivery, Bounce, SpamComplaint, Open, or Click;
	anything else — including SubscriptionChange, which is handled
	entirely by handle_subscription_change instead, see the module
	docstring — is a no-op (returns None). Dispatch on RecordType happens
	in subscriptions.views.postmark_webhook, which only calls this for the
	five types above, but this function stays defensive on its own via the
	EmailEvent.RECORD_TYPES check below.

	Never raises. Any exception — including a payload shaped unexpectedly —
	is logged and swallowed, so a bug here can never turn a webhook call
	into a non-200 response.

	Returns the created EmailEvent, or None when nothing was written: an
	unrecognised (or deliberately-excluded) RecordType, or a replay of an
	already-recorded (record_type, message_id, occurred_at, recipient,
	link_url).
	"""
	record_type = payload.get("RecordType")
	if record_type not in EmailEvent.RECORD_TYPES:
		return None

	try:
		recipient_field = _RECIPIENT_FIELD.get(record_type, "Recipient")
		recipient = (payload.get(recipient_field) or "").strip().lower()
		message_id = payload.get("MessageID") or ""
		occurred_at = _parse_occurred_at(record_type, payload)
		tag = payload.get("Tag") or ""
		message_stream = payload.get("MessageStream") or ""
		message = _find_message(payload)

		extra = {}
		if record_type in (
			EmailEvent.RECORD_TYPE_BOUNCE,
			EmailEvent.RECORD_TYPE_SPAM_COMPLAINT,
		):
			# Type/TypeCode/Details only — never Details' sibling `Content`,
			# which Postmark also embeds in Bounce payloads and which this
			# model deliberately never stores (see EmailEvent docstring).
			extra["bounce_type"] = payload.get("Type") or ""
			extra["bounce_type_code"] = payload.get("TypeCode")
			extra["details"] = payload.get("Details") or ""
		elif record_type == EmailEvent.RECORD_TYPE_CLICK:
			extra["link_url"] = payload.get("OriginalLink") or ""

		with transaction.atomic():
			event = EmailEvent.objects.create(
				email_message=message,
				record_type=record_type,
				message_id=message_id,
				recipient=recipient,
				occurred_at=occurred_at,
				tag=tag,
				message_stream=message_stream,
				**extra,
			)
			if message is not None:
				_update_message_aggregates(message, record_type, occurred_at, extra)

		# Not inside the transaction above: this is an independent write to
		# a different table (AuthorContactOptOut). record_author_opt_out
		# never raises on its own (see its docstring), but this call is
		# additionally wrapped on its own, separately from the outer
		# try/except below, so that even a hypothetical failure here can
		# never cost us the EmailEvent row already committed above — the
		# function still returns `event`, and a webhook caller still sees
		# it as processed, either way.
		try:
			if (
				record_type == EmailEvent.RECORD_TYPE_BOUNCE
				and extra.get("bounce_type") in HARD_BOUNCE_TYPES
			):
				record_author_opt_out(
					recipient,
					AuthorContactOptOut.REASON_HARD_BOUNCE,
					note=f"Postmark Bounce Type={extra.get('bounce_type')!r}.",
				)
			elif record_type == EmailEvent.RECORD_TYPE_SPAM_COMPLAINT:
				record_author_opt_out(recipient, AuthorContactOptOut.REASON_SPAM_COMPLAINT)
		except Exception:
			logger.exception(
				"email_events: failed to record an author opt-out for %s event "
				"(EmailEvent %s was still recorded).",
				record_type,
				event.pk,
			)

		return event
	except IntegrityError:
		return None  # replay of an already-recorded (record_type, message_id, occurred_at, recipient, link_url)
	except Exception:
		logger.exception("email_events: failed to process %s event.", record_type)
		return None
