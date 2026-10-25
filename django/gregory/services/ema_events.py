"""
Medicine events: what changed in a medicine's regulatory life.

Two kinds of event come out of a sync:

* ``reconstructed`` events are read off EMA's date fields when a medicine is first
  seen, so history exists from day one. Only the latest EC decision is in the
  export, so a product gets one ``ec_decision`` here, not every past variation.
* ``observed`` events are the difference between the stored row and a new export.

Both functions return plain dicts (``kind``, ``event_date``, ``basis``, ``from_value``,
``to_value``); the caller attaches the medicine and writes them with
``bulk_create(..., ignore_conflicts=True)``, which the unique constraint on
(medicine, kind, event_date) makes safe to repeat.
"""

from gregory.models import MedicineEvent

Kind = MedicineEvent.Kind
Basis = MedicineEvent.Basis

# A date column and the kind it sets. Opinion and EC decision are handled apart:
# their kind or condition varies.
_SIMPLE_DATES = (
	("rolling_review_start_date", Kind.ROLLING_REVIEW_STARTED),
	("evaluation_start_date", Kind.EVALUATION_STARTED),
	("authorisation_date", Kind.AUTHORISED),
	("refusal_date", Kind.REFUSED),
	("application_withdrawal_date", Kind.APPLICATION_WITHDRAWN),
	("suspension_date", Kind.SUSPENDED),
	("end_date", Kind.ENDED),
)

# Kinds that account for a change of status. EC decisions and indication edits
# do not: a product can get both without changing status.
_STATUS_EXPLAINING = {
	Kind.ROLLING_REVIEW_STARTED,
	Kind.EVALUATION_STARTED,
	Kind.OPINION_POSITIVE,
	Kind.OPINION_NEGATIVE,
	Kind.OPINION_ADOPTED,
	Kind.AUTHORISED,
	Kind.REFUSED,
	Kind.APPLICATION_WITHDRAWN,
	Kind.SUSPENDED,
	Kind.ENDED,
}


def opinion_kind(opinion_status, authorisation_date, status, refusal_date):
	"""The kind of a CHMP opinion event.

	EMA fills ``opinion_status`` only while a medicine is at the opinion stage and
	clears it once the EC decides, so most opinions have no stored outcome. When it is
	empty, a product that was authorised had a positive opinion and a refused one a
	negative opinion. Anything else is ``opinion_adopted``, which claims no outcome."""
	outcome = (opinion_status or "").strip().lower()
	if outcome == "positive":
		return Kind.OPINION_POSITIVE
	if outcome == "negative":
		return Kind.OPINION_NEGATIVE
	if authorisation_date:
		return Kind.OPINION_POSITIVE
	if refusal_date or (status or "").strip().lower() == "refused":
		return Kind.OPINION_NEGATIVE
	return Kind.OPINION_ADOPTED


def _event(kind, event_date, basis, from_value="", to_value=""):
	return {
		"kind": kind,
		"event_date": event_date,
		"basis": basis,
		"from_value": from_value,
		"to_value": to_value,
	}


def _ec_decision_counts(ec_date, authorisation_date):
	"""A decision dated the same day as the authorisation is the authorisation."""
	return ec_date is not None and ec_date != authorisation_date


def _parsed_opinion_kind(parsed):
	return opinion_kind(
		parsed.opinion_status, parsed.authorisation_date, parsed.status, parsed.refusal_date
	)


def reconstructed_events(parsed):
	"""Events for a medicine seen for the first time, from its date fields."""
	events = []
	for attr, kind in _SIMPLE_DATES:
		event_date = getattr(parsed, attr)
		if event_date is None:
			continue
		to_value = parsed.status if kind == Kind.ENDED else ""
		events.append(_event(kind, event_date, Basis.RECONSTRUCTED, to_value=to_value))
	if parsed.opinion_date is not None:
		events.append(
			_event(_parsed_opinion_kind(parsed), parsed.opinion_date, Basis.RECONSTRUCTED)
		)
	if _ec_decision_counts(parsed.latest_ec_decision_date, parsed.authorisation_date):
		events.append(
			_event(Kind.EC_DECISION, parsed.latest_ec_decision_date, Basis.RECONSTRUCTED)
		)
	return events


def observed_events(old_medicine, parsed, today):
	"""Events for what changed between the stored medicine and a new export row.

	A date that appears or moves gives its kind with that date. A changed indication
	gives ``indication_changed`` dated ``today``, with both texts. A status change that
	no other event explains gives ``status_changed``. ``opinion_status`` going from a
	value to empty is how EMA closes the opinion stage and is not an event."""
	events = []

	for attr, kind in _SIMPLE_DATES:
		new_date = getattr(parsed, attr)
		if new_date is not None and new_date != getattr(old_medicine, attr):
			to_value = parsed.status if kind == Kind.ENDED else ""
			events.append(_event(kind, new_date, Basis.OBSERVED, to_value=to_value))

	if parsed.opinion_date is not None:
		new_kind = _parsed_opinion_kind(parsed)
		old_kind = opinion_kind(
			old_medicine.opinion_status,
			old_medicine.authorisation_date,
			old_medicine.status,
			old_medicine.refusal_date,
		)
		date_moved = parsed.opinion_date != old_medicine.opinion_date
		# A kind that firms up (adopted -> positive) is news; one that softens because
		# EMA cleared opinion_status is not.
		firmed_up = new_kind != old_kind and new_kind != Kind.OPINION_ADOPTED
		if date_moved or firmed_up:
			events.append(_event(new_kind, parsed.opinion_date, Basis.OBSERVED))

	if (
		_ec_decision_counts(parsed.latest_ec_decision_date, parsed.authorisation_date)
		and parsed.latest_ec_decision_date != old_medicine.latest_ec_decision_date
	):
		events.append(
			_event(Kind.EC_DECISION, parsed.latest_ec_decision_date, Basis.OBSERVED)
		)

	if parsed.indication != old_medicine.indication:
		events.append(
			_event(
				Kind.INDICATION_CHANGED,
				today,
				Basis.OBSERVED,
				from_value=old_medicine.indication,
				to_value=parsed.indication,
			)
		)

	if parsed.status != old_medicine.status and not any(
		e["kind"] in _STATUS_EXPLAINING for e in events
	):
		events.append(
			_event(
				Kind.STATUS_CHANGED,
				today,
				Basis.OBSERVED,
				from_value=old_medicine.status,
				to_value=parsed.status,
			)
		)

	return events
