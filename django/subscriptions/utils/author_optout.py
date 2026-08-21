"""
Shared write path for AuthorContactOptOut — the global "never contact this
address again" list. See AuthorContactOptOut's model docstring
(subscriptions/models.py) and docs/author-outreach-spec.md "Legal basis and
consent" / "Bounce and complaint handling" for the full design.

Three independent callers write through record_author_opt_out() below, so
they can't drift from one another:

- subscriptions.utils.email_events.handle_email_event — a hard bounce
  (Postmark Bounce Type of HardBounce or BadEmailAddress) or a
  SpamComplaint.
- subscriptions.utils.postmark_webhook.handle_subscription_change — a
  suppressed address that matches an existing AuthorOutreach recipient.
- subscriptions.views.author_optout — the opt-out link's POST.

All three need this write to be unable to turn a success into a failure:
the Postmark webhook must keep returning 200 regardless of what happens
here (see subscriptions.views.postmark_webhook), and the opt-out view must
not surface an internal error to someone who just asked never to be
emailed again. record_author_opt_out therefore never raises.
"""

import logging

from django.db import IntegrityError

from subscriptions.models import AuthorContactOptOut, AuthorOutreach

logger = logging.getLogger(__name__)


def record_author_opt_out(email, reason, *, note=""):
	"""
	Write an AuthorContactOptOut row for `email`, or no-op if one already
	exists for that address (case-insensitively).

	Once an address is opted out, a later event with a different reason
	changes nothing: the address was never going to be contacted again
	either way, so the first recorded reason is kept rather than
	overwritten or duplicated.

	`author` is resolved best-effort from an existing AuthorOutreach row
	for the same address, when one exists — never required, since a
	bounce or spam complaint can arrive for an address that has never
	been an outreach recipient at all.

	Never raises — see the module docstring for why every caller depends
	on that. Returns the created (or already-existing) AuthorContactOptOut
	row, or None when `email` is empty or the write failed.
	"""
	normalized = (email or "").strip().lower()
	if not normalized:
		return None
	try:
		existing = AuthorContactOptOut.objects.filter(email__iexact=normalized).first()
		if existing is not None:
			return existing
		author_id = (
			AuthorOutreach.objects.filter(email__iexact=normalized)
			.order_by("-queued_at")
			.values_list("author_id", flat=True)
			.first()
		)
		return AuthorContactOptOut.objects.create(
			author_id=author_id,
			email=normalized,
			reason=reason,
			note=note,
		)
	except IntegrityError:
		# Race: another request/webhook call created the row between the
		# existence check above and create() (unique_author_optout_email).
		# The address is opted out either way — return the winning row.
		return AuthorContactOptOut.objects.filter(email__iexact=normalized).first()
	except Exception:
		logger.exception(
			"record_author_opt_out: failed to write opt-out row (reason=%s).",
			reason,
		)
		return None


# Postmark SubscriptionChange SuppressionReason -> the closest
# AuthorContactOptOut.reason bucket, used by handle_subscription_change
# when a suppressed address matches an existing AuthorOutreach recipient.
# ManualSuppression (and anything else Postmark might send, including a
# blank reason) maps to "admin": it means a human, not an automated
# bounce/complaint signal, suppressed the address on Postmark's side —
# closest in kind to a manually-added opt-out row.
_SUPPRESSION_REASON_TO_OPTOUT_REASON = {
	"HardBounce": AuthorContactOptOut.REASON_HARD_BOUNCE,
	"SpamComplaint": AuthorContactOptOut.REASON_SPAM_COMPLAINT,
}


def optout_reason_for_suppression_reason(suppression_reason):
	"""
	Map a Postmark SubscriptionChange SuppressionReason to the closest
	AuthorContactOptOut.reason bucket. See
	_SUPPRESSION_REASON_TO_OPTOUT_REASON above for the mapping and why
	anything else falls back to "admin".
	"""
	return _SUPPRESSION_REASON_TO_OPTOUT_REASON.get(
		suppression_reason, AuthorContactOptOut.REASON_ADMIN
	)
