"""Per-source replace helper for TrialSite rows.

Shared by the CTIS retrieve enrichment (feedreader_trials_ctis.py) and the
ClinicalTrials.gov site capture (feedreader_trials_ctgov.py /
backfill_trial_sites_from_ctgov.py) so neither importer can wipe the other's
sites for a trial captured by both registries — see TRIAL-GEOGRAPHY-PLAN.md
PR G2 §2.2. Before this helper existed, CTIS enrichment replaced *all*
TrialSite rows for a trial regardless of source, so running CTGov capture
after it (or vice versa) would silently delete the other registry's sites on
every run.
"""

from django.db import transaction

from gregory.models import TrialSite


def replace_trial_sites(trial, source: str, rows: list[dict]) -> None:
	"""Replace only *source*'s TrialSite rows for *trial*, leaving any other
	source's rows untouched. *rows* are dicts of TrialSite field kwargs
	(without `trial` or `sources`, which are set here). Runs in one
	transaction so a mid-batch failure never leaves the trial's *source*
	sites half-deleted."""
	with transaction.atomic():
		TrialSite.objects.filter(trial=trial, sources__contains=[source]).delete()
		TrialSite.objects.bulk_create(
			[TrialSite(trial=trial, sources=[source], **row) for row in rows],
			batch_size=500,
		)
