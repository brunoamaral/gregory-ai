"""One-time fold: recompute every SponsorAlias.key with the hardened
normalize_sponsor_key() (full punctuation stripping, not just trailing) and fold any
sponsors that collide as a result.

Idempotent and safe to re-run: once every alias carries its hardened key, a second run
finds no cross-sponsor collisions and no key drift, so it is a no-op. See
SPONSOR-DUPLICATE-RESOLUTION-PLAN.md PR D1.

Usage:
    python manage.py recompute_sponsor_alias_keys --dry-run
    python manage.py recompute_sponsor_alias_keys
"""

from collections import defaultdict

from django.core.management.base import BaseCommand

from gregory.models import Sponsor, SponsorAlias
from gregory.utils.sponsor_merge import merge_sponsors
from gregory.utils.trial_field_normalizers import normalize_sponsor_key


def _pick_survivor(sponsors: list[Sponsor]) -> Sponsor:
	"""Curated sponsors win; otherwise the sponsor with the most trials; ties go to
	the lowest id. A curated survivor must never lose to a bigger but uncurated
	duplicate (e.g. a stray auto-created singleton)."""
	curated = [s for s in sponsors if s.sponsor_type_source == "curated"]
	pool = curated if curated else sponsors
	return max(pool, key=lambda s: (s.trials.count(), -s.pk))


def _connected_components(groups: dict) -> list[set]:
	"""Union-find over sponsor ids: two sponsors are linked whenever any hardened key
	groups their aliases together. A sponsor can hold aliases spanning several distinct
	hardened keys, each colliding with a different set of other sponsors — folding key
	group by key group independently (rather than by full connected component) risks
	repointing a trial onto a sponsor a prior, unrelated fold already deleted. Computing
	full components up front and merging each one exactly once avoids that."""
	parent: dict[int, int] = {}

	def find(x: int) -> int:
		root = x
		while parent[root] != root:
			root = parent[root]
		while parent[x] != root:
			parent[x], x = root, parent[x]
		return root

	def union(a: int, b: int) -> None:
		ra, rb = find(a), find(b)
		if ra != rb:
			parent[ra] = rb

	for alias_list in groups.values():
		sponsor_ids = {a.sponsor_id for a in alias_list}
		for sid in sponsor_ids:
			parent.setdefault(sid, sid)
		ids_list = list(sponsor_ids)
		for other_id in ids_list[1:]:
			union(ids_list[0], other_id)

	components: dict[int, set] = defaultdict(set)
	for sid in parent:
		components[find(sid)].add(sid)
	return [ids for ids in components.values() if len(ids) > 1]


class Command(BaseCommand):
	help = (
		"Recompute SponsorAlias.key with the hardened (fully punctuation-insensitive) "
		"normalize_sponsor_key(), folding any sponsors whose aliases now collide."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Report the fold groups without making changes.",
		)

	def handle(self, *args, **options):
		dry_run = options["dry_run"]

		aliases = list(SponsorAlias.objects.select_related("sponsor").all())
		groups: dict[str, list[SponsorAlias]] = defaultdict(list)
		for alias in aliases:
			new_key = normalize_sponsor_key(alias.raw_sample)
			if new_key is None:
				continue
			groups[new_key].append(alias)

		fold_groups = []
		for sponsor_ids in _connected_components(groups):
			sponsors = list(Sponsor.objects.filter(pk__in=sponsor_ids))
			survivor = _pick_survivor(sponsors)
			others = [s for s in sponsors if s.pk != survivor.pk]
			fold_groups.append((survivor, others))

		trials_repointed = 0
		sponsors_folded = 0
		for survivor, others in fold_groups:
			trial_counts = {o.pk: o.trials.count() for o in others}
			self.stdout.write(
				f"FOLD: {survivor.name!r} (id={survivor.pk}) <- "
				+ ", ".join(
					f"{o.name!r} (id={o.pk}, trials={trial_counts[o.pk]})" for o in others
				)
			)
			if dry_run:
				trials_repointed += sum(trial_counts.values())
				sponsors_folded += len(others)
			else:
				repointed, _ = merge_sponsors(survivor, others)
				trials_repointed += repointed
				sponsors_folded += len(others)

		if dry_run:
			self.stdout.write(
				self.style.WARNING(
					f"DRY RUN — would fold {len(fold_groups)} group(s), "
					f"{sponsors_folded} sponsor(s) absorbed, {trials_repointed} trial(s) "
					"repointed. No changes made."
				)
			)
			return

		# Re-key every alias now that the fold above has resolved every cross-sponsor
		# collision under the hardened key. Reload since merge_sponsors moved/deleted
		# rows above.
		aliases = list(SponsorAlias.objects.all())
		by_sponsor_key: dict[tuple[int, str], list[SponsorAlias]] = defaultdict(list)
		for alias in aliases:
			new_key = normalize_sponsor_key(alias.raw_sample)
			if new_key is None:
				continue
			by_sponsor_key[(alias.sponsor_id, new_key)].append(alias)

		to_update = []
		to_delete_ids = []
		for (_sponsor_id, new_key), alias_list in by_sponsor_key.items():
			alias_list.sort(key=lambda a: a.pk)
			survivor_alias, *dupes = alias_list
			if survivor_alias.key != new_key:
				survivor_alias.key = new_key
				to_update.append(survivor_alias)
			to_delete_ids.extend(a.pk for a in dupes)

		if to_delete_ids:
			SponsorAlias.objects.filter(pk__in=to_delete_ids).delete()

		batch_size = 500
		for i in range(0, len(to_update), batch_size):
			SponsorAlias.objects.bulk_update(
				to_update[i : i + batch_size], ["key"], batch_size=batch_size
			)

		self.stdout.write(
			self.style.SUCCESS(
				f"Folded {len(fold_groups)} group(s): {sponsors_folded} sponsor(s) "
				f"absorbed, {trials_repointed} trial(s) repointed. Re-keyed "
				f"{len(to_update)} alias(es), removed {len(to_delete_ids)} now-duplicate "
				"alias(es)."
			)
		)
