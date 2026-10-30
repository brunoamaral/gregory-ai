"""
Medicine events for FDA rows: what changed in an application's regulatory life.

Two kinds of event come out of a sync, as for EMA (see ``ema_events``):

* ``reconstructed`` events are read off the FDA's own dates: the original approval,
  tentative approvals and approved efficacy supplements of an application, each dated
  with its submission's status date. A medicine seen for the first time gets all of them,
  so history exists from day one.
* ``observed`` events are the difference between the stored row and a new file: an
  application whose products all became discontinued, a biologic whose licence was
  revoked, any other status change.

The functions return plain dicts (``kind``, ``event_date``, ``basis``, ``from_value``,
``to_value``); the caller attaches the medicine and writes them with
``bulk_create(..., ignore_conflicts=True)``, which the unique constraint on
(medicine, kind, event_date) makes safe to repeat.

A dated event first seen on an existing medicine is ``observed`` only when its date is
within ``OBSERVED_WINDOW_DAYS`` of today. Drugs@FDA sometimes lists an old submission
late, and an old event marked ``observed`` would surface in the feeds as news.
"""

from datetime import timedelta

from gregory.models import MedicineEvent
from gregory.utils import fda

Kind = MedicineEvent.Kind
Basis = MedicineEvent.Basis

OBSERVED_WINDOW_DAYS = 90


def dated_events(parsed):
	"""The events a parsed application's submissions give, without a basis.

	An NDA or CDER BLA gives its original approval, tentative approvals and approved
	efficacy supplements; a CBER BLA gives one ``fda_approved`` dated with its
	``authorisation_date``. Two approvals of one kind on the same day are one event."""
	return [dict(event) for event in parsed.events]


def _with_basis(event, basis):
	return {**event, "basis": basis}


def reconstructed_events(parsed):
	"""Events for a medicine seen for the first time: all of them are reconstructed."""
	return [_with_basis(event, Basis.RECONSTRUCTED) for event in dated_events(parsed)]


def new_dated_events(parsed, stored, today):
	"""Dated events of an existing medicine that are not stored yet.

	``stored`` is the set of ``(kind, event_date)`` pairs already in the table. A new one is
	``observed`` when its date is within 90 days of ``today`` (or later), otherwise
	``reconstructed``."""
	recent_since = today - timedelta(days=OBSERVED_WINDOW_DAYS)
	events = []
	for event in dated_events(parsed):
		if (event["kind"], event["event_date"]) in stored:
			continue
		basis = Basis.OBSERVED if event["event_date"] >= recent_since else Basis.RECONSTRUCTED
		events.append(_with_basis(event, basis))
	return events


def _status_event(kind, today, from_value, to_value):
	return {
		"kind": kind,
		"event_date": today,
		"basis": Basis.OBSERVED,
		"from_value": from_value,
		"to_value": to_value,
	}


def observed_status_events(old, parsed, today):
	"""Events for a change of status between the stored medicine ``old`` and ``parsed``.

	* ``fda_discontinued`` when an approved or tentatively approved application becomes
	  discontinued (every product is);
	* ``fda_revoked`` when a Purple Book licence becomes revoked or voluntarily revoked;
	* ``status_changed`` for any other change, with both values. Becoming approved after a
	  tentative approval is not one: the new ``fda_approved`` event says it.

	Dated ``today``, the day the sync saw the change."""
	if parsed.status == old.status:
		return []
	live = (fda.APPROVED, fda.TENTATIVELY_APPROVED)
	if parsed.status == fda.REVOKED:
		return [_status_event(Kind.FDA_REVOKED, today, old.status, parsed.status)]
	if parsed.status == fda.DISCONTINUED and old.status in live:
		return [_status_event(Kind.FDA_DISCONTINUED, today, old.status, parsed.status)]
	if (
		old.status == fda.TENTATIVELY_APPROVED
		and parsed.status == fda.APPROVED
		and any(event["kind"] == fda.FDA_APPROVED for event in parsed.events)
	):
		return []
	return [_status_event(Kind.STATUS_CHANGED, today, old.status, parsed.status)]
