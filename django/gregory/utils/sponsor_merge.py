"""Shared Sponsor merge routine.

Extracted from the merge_sponsors management command so the same logic backs every
caller that folds one Sponsor into another: the merge_sponsors command itself,
sync_sponsor_seeds' stray-sponsor fold path, recompute_sponsor_alias_keys (PR D1), and
the SponsorMergeCandidate admin merge action (PR D2). One implementation, several
callers.
"""

from django.db import transaction

from gregory.models import Sponsor, SponsorAlias


def merge_sponsors(target: Sponsor, others: list[Sponsor]) -> tuple[int, int]:
	"""Merge `others` into `target`: repoint their trials and aliases onto `target`,
	carry `sponsor_type`/`sponsor_type_source` over to `target` only when it doesn't
	already have one, then delete the emptied sponsors. Trials are repointed before
	the source sponsors are deleted, since Trials.primary_sponsor_normalized is
	on_delete=PROTECT. Returns (trials_repointed, aliases_moved)."""
	total_trials = 0
	total_aliases = 0
	target_changed = False
	with transaction.atomic():
		for source in others:
			total_trials += source.trials.update(primary_sponsor_normalized=target)
			total_aliases += SponsorAlias.objects.filter(sponsor=source).update(sponsor=target)
			if not target.sponsor_type and source.sponsor_type:
				target.sponsor_type = source.sponsor_type
				target.sponsor_type_source = source.sponsor_type_source
				target_changed = True
			source.delete()
		if target_changed:
			target.save(update_fields=["sponsor_type", "sponsor_type_source"])
	return total_trials, total_aliases
