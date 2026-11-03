"""
Linking a patent to a clinical trial, by detection or by hand.

The one place that knows the rules for a patent-trial link, shared by the command
``detect_patent_trial_links``, the MCP editor endpoints (``api/editor_views.py``) and
the Django admin trial and patent pages:

- Detection (``source="auto"``) links a sponsor's patent to its trial when the patent was
  first filed during the trial or up to 24 months after it. ``trial_window`` and
  ``classify`` hold those rules.
- An editor adds a ``source="manual"`` row. Its sponsor, timing, days and basis are worked
  out once, when the link is made (``describe_link``), and any of them may be NULL: the
  patent and the trial need not share a sponsor, a subject, or even known dates. A
  patent filed before the trial is ``before``, and ``after`` has no 24-month limit.
- A pair that already has a visible link (manual or auto) gets nothing new. A hidden auto
  row is brought back and becomes manual, since the editor now vouches for it.
- Removing deletes a manual row. A detected row is kept and marked ``suppressed``, so
  ``detect_patent_trial_links`` does not recreate it.

These functions do not choose who gets recorded in the history: the caller does. The
editor endpoints wrap them in ``editing_as(user, "mcp")``; the admin calls them inside
the request, where simple-history's middleware records ``via="admin"``.
"""

from datetime import date

from dateutil.relativedelta import relativedelta
from django.db import transaction

from gregory.models import PatentTrialLink
from gregory.utils.trial_field_normalizers import TrialRecruitmentStatus

ONGOING_STATUSES = frozenset(
	{
		TrialRecruitmentStatus.NOT_YET_RECRUITING.value,
		TrialRecruitmentStatus.RECRUITING.value,
		TrialRecruitmentStatus.ENROLLING_BY_INVITATION.value,
		TrialRecruitmentStatus.ACTIVE_NOT_RECRUITING.value,
		TrialRecruitmentStatus.SUSPENDED.value,
	}
)
DEFAULT_AFTER_MONTHS = 24


def trial_window(trial):
	"""(start, end) of a trial, where end is None for an open window. Returns None when
	the window cannot be determined or the trial is withdrawn."""
	status = trial.recruitment_status_normalized
	if status == TrialRecruitmentStatus.WITHDRAWN.value:
		return None
	start = trial.date_enrollement or (
		trial.published_date.date() if trial.published_date else None
	)
	if start is None:
		return None
	if status in ONGOING_STATUSES:
		return start, None
	end = trial.primary_completion_date or trial.completion_date
	if end is None:
		return None
	return start, end


def classify(priority_date: date, window, after_months=DEFAULT_AFTER_MONTHS):
	"""Return (timing, days_after_completion) for a priority date against a trial window,
	or None when there is no signal. This is detection's rule: it never returns
	``before``, and ``after`` stops ``after_months`` after the trial's end."""
	if window is None or priority_date is None:
		return None
	start, end = window
	if priority_date < start:
		return None
	if end is None or priority_date <= end:
		return PatentTrialLink.TIMING_DURING, None
	if priority_date <= end + relativedelta(months=after_months):
		return PatentTrialLink.TIMING_AFTER, (priority_date - end).days
	return None


def shared_sponsor_id(patent, trial):
	"""The trial's sponsor when it is also one of the patent's applicants, else None."""
	trial_sponsor_id = trial.primary_sponsor_normalized_id
	if trial_sponsor_id is None:
		return None
	applicant_sponsor_ids = set(
		patent.patent_applicants.filter(sponsor__isnull=False).values_list("sponsor_id", flat=True)
	)
	return trial_sponsor_id if trial_sponsor_id in applicant_sponsor_ids else None


def shared_basis(patent, trial):
	"""``category`` when the patent and the trial share a category, ``subject`` when only
	a subject, else None."""
	if patent.categories.filter(pk__in=trial.categories.values("pk")).exists():
		return PatentTrialLink.BASIS_CATEGORY
	if patent.subjects.filter(pk__in=trial.subjects.values("pk")).exists():
		return PatentTrialLink.BASIS_SUBJECT
	return None


def describe_link(patent, trial) -> dict:
	"""What a manual link between the pair records, worked out from the data as it stands.

	Keys ``sponsor_id``, ``timing``, ``days_after_completion``, ``days_before_start`` and
	``basis``. Unlike detection's ``classify``, a patent filed before the trial is
	``before`` and ``after`` has no time limit. ``timing`` is None when the patent has no
	priority date or the trial's dates cannot be determined."""
	values = {
		"sponsor_id": shared_sponsor_id(patent, trial),
		"timing": None,
		"days_after_completion": None,
		"days_before_start": None,
		"basis": shared_basis(patent, trial),
	}
	window = trial_window(trial)
	priority_date = patent.earliest_priority_date
	if window is None or priority_date is None:
		return values
	start, end = window
	if priority_date < start:
		values["timing"] = PatentTrialLink.TIMING_BEFORE
		values["days_before_start"] = (start - priority_date).days
	elif end is None or priority_date <= end:
		values["timing"] = PatentTrialLink.TIMING_DURING
	else:
		values["timing"] = PatentTrialLink.TIMING_AFTER
		values["days_after_completion"] = (priority_date - end).days
	return values


def patent_label(patent) -> str:
	"""How a patent reads in the admin picker and the link list: ``Patents.__str__`` is
	only the title.

	``"<representative publication> · <title, truncated>"``, leaving out what is empty.
	"""
	title = (patent.title or "").strip()
	if len(title) > 90:
		title = title[:89].rstrip() + "…"
	publication = (patent.representative_publication or "").strip()
	return " · ".join(part for part in (publication, title) if part)


def visible_link(patent, trial):
	"""The visible (not suppressed) link between the pair, or None."""
	return PatentTrialLink.objects.filter(patent=patent, trial=trial, suppressed=False).first()


def link_patent_trial(patent, trial, user):
	"""Link ``trial`` to ``patent`` by hand. Returns ``(link, created)``.

	Idempotent: a pair that already has a visible link returns that link and creates
	nothing. A hidden detected row is restored and becomes manual (``created_by=user``),
	keeping its timing, basis and sponsor; the history records both changes.
	"""
	with transaction.atomic():
		existing = PatentTrialLink.objects.select_for_update().filter(patent=patent, trial=trial).first()
		if existing is None:
			# get_or_create on the unique (patent, trial) key, so two identical
			# requests at once both answer instead of one 500ing.
			return PatentTrialLink.objects.get_or_create(
				patent=patent,
				trial=trial,
				defaults={
					**describe_link(patent, trial),
					"source": PatentTrialLink.SOURCE_MANUAL,
					"created_by": user,
				},
			)
		if not existing.suppressed:
			return existing, False
		existing.suppressed = False
		existing.source = PatentTrialLink.SOURCE_MANUAL
		existing.created_by = user
		existing.save()
		return existing, False


def remove_patent_trial_link(link):
	"""Remove one link: delete a manual row, hide a detected one (``suppressed=True``)."""
	if link.source == PatentTrialLink.SOURCE_MANUAL:
		link.delete()
	else:
		link.suppressed = True
		link.save()


def unlink_patent_trial(patent, trial):
	"""Remove the visible link between ``patent`` and ``trial``: a manual link is
	deleted, a detected one hidden. Returns the removed link's source, or None when the
	pair had no visible link."""
	with transaction.atomic():
		link = (
			PatentTrialLink.objects.select_for_update()
			.filter(patent=patent, trial=trial, suppressed=False)
			.first()
		)
		if link is None:
			return None
		source = link.source
		remove_patent_trial_link(link)
		return source
