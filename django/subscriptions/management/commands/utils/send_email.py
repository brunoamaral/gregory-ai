import logging
import uuid

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def send_email(
	to,
	subject,
	html,
	text,
	site,
	sender_name="Gregory AI",
	api_token=None,
	api_url=None,
	sender_prefix=None,
	tag=None,
	metadata=None,
	reply_to=None,
	track_opens=False,
	track_links=False,
):
	"""
	Sends an email using the Postmark API.

	:param to: Recipient email address.
	:param subject: Email subject.
	:param html: HTML body of the email.
	:param text: Plain text body of the email.
	:param site: Site object for generating the sender email.
	:param sender_name: Name of the sender (default is "GregoryAI").
	:param api_token: Custom Postmark API token (if provided).
	:param api_url: Custom Postmark API URL (if provided).
	:param sender_prefix: Local part of the sender address (default: 'gregory').
	:param tag: Postmark message tag (e.g. "weekly_summary"). Postmark allows
		exactly one tag per message; used for Postmark-side stats/debugging
		only, not for suppression, which keys off the recipient address.
	:param metadata: Optional dict merged into Postmark's Metadata field
		(e.g. {"msg_token": "<uuid4>", "campaign": "<slug>"} — see
		EmailMessage.msg_token). None by default, so every existing caller's
		payload is byte-for-byte unchanged.
	:param reply_to: Optional Reply-To address. None by default — omitted
		from the payload entirely rather than sent empty.
	:param track_opens: Postmark TrackOpens. False by default, matching
		today's behaviour for every existing sender; digest/admin/trial/
		announcement email must keep NOT tracking opens unless a caller
		opts in explicitly.
	:param track_links: Postmark TrackLinks. False by default for the same
		reason. When True, sent as "HtmlAndText" (Postmark tracks links in
		both bodies rather than picking one).
	:return: Response object from the Postmark API.
	"""
	prefix = sender_prefix or "gregory"
	sender = f"{sender_name} <{prefix}@{site.domain}>"
	email_postmark_api_url = api_url or settings.EMAIL_POSTMARK_API_URL

	# Use the provided API token or fall back to the default from settings
	postmark_api_token = api_token or settings.EMAIL_POSTMARK_API_KEY

	payload = {
		"MessageStream": "broadcast",
		"From": sender,
		"To": to,
		"Subject": subject,
		"TextBody": text,
		"HtmlBody": html,
	}
	if tag:
		payload["Tag"] = tag
	if metadata:
		payload["Metadata"] = metadata
	if reply_to:
		payload["ReplyTo"] = reply_to
	if track_opens:
		payload["TrackOpens"] = True
	if track_links:
		payload["TrackLinks"] = "HtmlAndText"

	response = requests.post(
		email_postmark_api_url,
		headers={
			"Accept": "application/json",
			"Content-Type": "application/json",
			"X-Postmark-Server-Token": postmark_api_token,
		},
		json=payload,
		timeout=30,
	)

	return response


def record_sent_message(
	response,
	*,
	recipient,
	subject,
	tag,
	stream="broadcast",
	site=None,
	subscriber=None,
	msg_token=None,
):
	"""
	Write the EmailMessage row for one send_email() attempt.

	Call this right after send_email() — on success and on failure alike
	(`response` may be None, e.g. when the caller caught a
	requests.RequestException before a response ever came back). This is
	the only place that turns a Postmark send into the durable local record
	a later webhook call (subscriptions.utils.email_events.handle_email_event)
	correlates against.

	Deliberately failure-tolerant: any exception here is logged and
	swallowed, never raised. A bug in this logging path must never take
	down a send that has already happened (or already been handled as a
	failure by the caller) — the send itself, and the caller's own
	FailedNotification/AnnouncementRecipient bookkeeping, always win.
	"""
	# Imported here, not at module level: subscriptions.models pulls in a
	# fair amount (simple_history, gregory.models, ...), and every sender
	# already imports this module at Django app-loading time.
	from subscriptions.models import EmailMessage
	from subscriptions.utils.postmark import classify_postmark_response

	try:
		accepted, error_code, detail = classify_postmark_response(response)

		message_id = ""
		if response is not None:
			body = response if isinstance(response, dict) else None
			if body is None:
				try:
					body = response.json()
				except ValueError:
					body = {}
			message_id = body.get("MessageID") or ""

		EmailMessage.objects.create(
			msg_token=msg_token or uuid.uuid4(),
			message_id=message_id,
			recipient=(recipient or "").strip().lower(),
			subject=subject or "",
			tag=tag or "",
			message_stream=stream or "",
			site=site,
			subscriber=subscriber,
			accepted=accepted,
			error_code=error_code if isinstance(error_code, int) else None,
			error_message="" if accepted else (detail or ""),
		)
	except Exception:
		logger.exception(
			"record_sent_message: failed to record EmailMessage for %s", recipient
		)
