"""Shared Sponsor merge routine.

Extracted from the merge_sponsors management command so the same logic backs every
caller that folds one Sponsor into another: the merge_sponsors command itself,
sync_sponsor_seeds' stray-sponsor fold path, recompute_sponsor_alias_keys (PR D1), and
the SponsorMergeCandidate admin merge action (PR D2). One implementation, several
callers.
"""

from django.db import transaction

from gregory.models import Sponsor, SponsorAlias, _SPONSOR_TYPE_SOURCE_PRIORITY


def _best_sponsor_type(sponsors: list[Sponsor]) -> tuple[str | None, str | None]:
	"""Deterministic pick among `sponsors` that have a sponsor_type set: highest
	_SPONSOR_TYPE_SOURCE_PRIORITY, then lowest id — so the result never depends on
	queryset/list ordering. Returns (None, None) if none of them has a type."""
	typed = [s for s in sponsors if s.sponsor_type]
	if not typed:
		return None, None
	best = max(
		typed,
		key=lambda s: (_SPONSOR_TYPE_SOURCE_PRIORITY.get(s.sponsor_type_source, -1), -s.pk),
	)
	return best.sponsor_type, best.sponsor_type_source


def merge_sponsors(target: Sponsor, others: list[Sponsor]) -> tuple[int, int]:
	"""Merge `others` into `target`: repoint their trials and aliases onto `target`,
	carry `sponsor_type`/`sponsor_type_source` over to `target` only when it doesn't
	already have one — picking deterministically among `others` via
	_best_sponsor_type() rather than whichever happens to be processed first — then
	delete the emptied sponsors. Trials are repointed before the source sponsors are
	deleted, since Trials.primary_sponsor_normalized is on_delete=PROTECT. Returns
	(trials_repointed, aliases_moved)."""
	total_trials = 0
	total_aliases = 0
	with transaction.atomic():
		if not target.sponsor_type:
			best_type, best_source = _best_sponsor_type(others)
			if best_type is not None:
				target.sponsor_type = best_type
				target.sponsor_type_source = best_source
				target.save(update_fields=["sponsor_type", "sponsor_type_source"])
		for source in others:
			total_trials += source.trials.update(primary_sponsor_normalized=target)
			total_aliases += SponsorAlias.objects.filter(sponsor=source).update(sponsor=target)
			source.delete()
	return total_trials, total_aliases
