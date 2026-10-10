"""Detect the sponsor-timing signal: a trial sponsor filing a patent while one of its
trials is ongoing or recently closed.

For each patent with a resolved applicant sponsor, the sponsor's trials are compared with
the patent's earliest priority date P (the date the sponsor first filed):

- Start S: the trial's enrolment start (date_enrollement), else its published date.
- End E: primary_completion_date, else completion_date. For a trial in an ongoing status
  (not yet recruiting, recruiting, enrolling by invitation, active not recruiting,
  suspended) E is open.
- ``during``: S <= P <= E (or P >= S for an open window).
- ``after``: E < P <= E + 24 months (--after-months).
- Anything else, withdrawn trials and trials whose dates cannot be determined produce no
  link.

The patent and the trial must also share a subject: the same sponsor in the same subject
alone is noisy for large sponsors, so ``basis="category"`` (a shared team category,
typically the molecule) marks the strong signal and ``basis="subject"`` the weak one.

Patent applications publish about 18 months after the priority date, so a filing made
during a trial becomes visible up to 18 months later: the signal answers "did the
sponsor file during or just after the trial", not "is it filing right now".

Links an editor suppressed are never recreated or modified.
"""

from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from django.core.management.base import BaseCommand
from django.utils import timezone

from gregory.models import PatentTrialLink, Patents, Trials
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
	or None when there is no signal."""
	if window is None or priority_date is None:
		return None
	start, end = window
	if priority_date < start:
		return None
	if end is None or priority_date <= end:
		return "during", None
	if priority_date <= end + relativedelta(months=after_months):
		return "after", (priority_date - end).days
	return None


class Command(BaseCommand):
	help = "Detects patents filed by a trial's sponsor during or shortly after the trial and creates PatentTrialLink rows."

	def add_arguments(self, parser):
		parser.add_argument("--patent-id", type=int, help="Process one patent.")
		parser.add_argument(
			"--recent",
			action="store_true",
			help="Only patents discovered in the last --days days.",
		)
		parser.add_argument("--days", type=int, default=30, help="Look-back for --recent (default 30).")
		parser.add_argument(
			"--after-months",
			type=int,
			default=DEFAULT_AFTER_MONTHS,
			help=f"Months after a trial's completion that still count as 'after' (default {DEFAULT_AFTER_MONTHS}).",
		)
		parser.add_argument(
			"--reset",
			action="store_true",
			help="Delete the existing non-suppressed links of the selected patents before scanning.",
		)
		parser.add_argument("--dry-run", action="store_true", help="Report without writing.")

	def handle(self, *args, **options):
		dry_run = options["dry_run"]
		after_months = options["after_months"]

		patents = Patents.objects.filter(
			earliest_priority_date__isnull=False, patent_applicants__sponsor__isnull=False
		).distinct()
		if options["patent_id"]:
			patents = patents.filter(pk=options["patent_id"])
		elif options["recent"]:
			patents = patents.filter(
				discovery_date__gte=timezone.now() - timedelta(days=options["days"])
			)
		patents = patents.prefetch_related("subjects", "team_categories", "patent_applicants")

		if options["reset"]:
			resettable = PatentTrialLink.objects.filter(
				suppressed=False, patent__in=patents
			)
			count = resettable.count()
			if dry_run:
				self.stdout.write(f"Would delete {count} existing links (dry run)")
			else:
				resettable.delete()
				self.stdout.write(f"Deleted {count} existing links")

		trials_by_sponsor = {}

		def sponsor_trials(sponsor_id):
			if sponsor_id not in trials_by_sponsor:
				trials_by_sponsor[sponsor_id] = list(
					Trials.objects.filter(primary_sponsor_normalized_id=sponsor_id).prefetch_related(
						"subjects", "team_categories"
					)
				)
			return trials_by_sponsor[sponsor_id]

		created = updated = removed = skipped_suppressed = 0
		patent_count = 0

		for patent in patents:
			patent_count += 1
			patent_subjects = {s.pk for s in patent.subjects.all()}
			patent_categories = {c.pk for c in patent.team_categories.all()}
			sponsor_ids = {
				a.sponsor_id for a in patent.patent_applicants.all() if a.sponsor_id
			}

			wanted = {}
			for sponsor_id in sponsor_ids:
				for trial in sponsor_trials(sponsor_id):
					if not patent_subjects & {s.pk for s in trial.subjects.all()}:
						continue
					result = classify(patent.earliest_priority_date, trial_window(trial), after_months)
					if result is None:
						continue
					timing, days_after = result
					shared_category = patent_categories & {c.pk for c in trial.team_categories.all()}
					wanted[trial.pk] = {
						"sponsor_id": sponsor_id,
						"timing": timing,
						"days_after_completion": days_after,
						"basis": PatentTrialLink.BASIS_CATEGORY if shared_category else PatentTrialLink.BASIS_SUBJECT,
					}

			existing = {link.trial_id: link for link in PatentTrialLink.objects.filter(patent=patent)}

			for trial_id, values in wanted.items():
				link = existing.get(trial_id)
				if link is None:
					created += 1
					if not dry_run:
						PatentTrialLink.objects.create(patent=patent, trial_id=trial_id, **values)
				elif link.suppressed:
					skipped_suppressed += 1
				elif any(getattr(link, key) != value for key, value in values.items()):
					updated += 1
					if not dry_run:
						for key, value in values.items():
							setattr(link, key, value)
						link.save()

			stale = [
				link for trial_id, link in existing.items() if trial_id not in wanted and not link.suppressed
			]
			removed += len(stale)
			if stale and not dry_run:
				PatentTrialLink.objects.filter(pk__in=[link.pk for link in stale]).delete()

		prefix = "Would " if dry_run else ""
		self.stdout.write(
			self.style.SUCCESS(
				f"{prefix}create {created}, update {updated}, remove {removed} link(s) across "
				f"{patent_count} patent(s); {skipped_suppressed} suppressed link(s) left alone."
			)
		)
