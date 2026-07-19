"""One-time fold: recompute every SponsorAlias.key with the hardened
normalize_sponsor_key() (full punctuation stripping, not just trailing) and fold any
sponsors that collide as a result.

Idempotent and safe to re-run: once every alias carries its hardened key, a second run
finds no cross-sponsor collisions and no key drift, so it is a no-op. See
SPONSOR-SURFACE-PLAN.md PR D1.

Usage:
    python manage.py recompute_sponsor_alias_keys --dry-run
    python manage.py recompute_sponsor_alias_keys
"""

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db.models import Count

from gregory.models import Sponsor, SponsorAlias
from gregory.utils.sponsor_merge import merge_sponsors
from gregory.utils.trial_field_normalizers import normalize_sponsor_key


def _pick_survivor(sponsors: list[Sponsor]) -> Sponsor:
	"""Curated sponsors win; otherwise the sponsor with the most trials (via the
	`trials_count` annotation callers must attach); ties go to the lowest id. A
	curated survivor must never lose to a bigger but uncurated duplicate (e.g. a
	stray auto-created singleton)."""
	curated = [s for s in sponsors if s.sponsor_type_source == "curated"]
	pool = curated if curated else sponsors
	return max(pool, key=lambda s: (s.trials_count, -s.pk))


def _connected_components(sponsor_ids_by_key: dict) -> list[set]:
	"""Union-find over sponsor ids: two sponsors are linked whenever any hardened key
	groups their aliases together. A sponsor can hold aliases spanning several distinct
	hardened keys, each colliding with a different set of other sponsors — folding key
	group by key group independently (rather than by full connected component) risks
	repointing a trial onto a sponsor a prior, unrelated fold already deleted. Computing
	full components up front and merging each one exactly once avoids that.

	`sponsor_ids_by_key` maps hardened key -> set of sponsor ids (not alias instances —
	only the ids matter for building the collision graph)."""
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

	for sponsor_ids in sponsor_ids_by_key.values():
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

		# Pass 1: group sponsor ids by hardened key. Streamed via values_list().iterator()
		# and keyed by id, not by full SponsorAlias/Sponsor instances — memory stays
		# proportional to the number of distinct keys and sponsors, not the full alias
		# table held as model objects.
		sponsor_ids_by_key: dict[str, set] = defaultdict(set)
		alias_rows = SponsorAlias.objects.values_list("sponsor_id", "raw_sample").iterator(
			chunk_size=2000
		)
		for sponsor_id, raw_sample in alias_rows:
			new_key = normalize_sponsor_key(raw_sample)
			if new_key is None:
				continue
			sponsor_ids_by_key[new_key].add(sponsor_id)

		fold_groups = []
		for sponsor_ids in _connected_components(sponsor_ids_by_key):
			sponsors = list(
				Sponsor.objects.filter(pk__in=sponsor_ids).annotate(
					trials_count=Count("trials", distinct=True)
				)
			)
			survivor = _pick_survivor(sponsors)
			others = [s for s in sponsors if s.pk != survivor.pk]
			fold_groups.append((survivor, others))

		trials_repointed = 0
		sponsors_folded = 0
		for survivor, others in fold_groups:
			self.stdout.write(
				f"FOLD: {survivor.name!r} (id={survivor.pk}) <- "
				+ ", ".join(
					f"{o.name!r} (id={o.pk}, trials={o.trials_count})" for o in others
				)
			)
			if dry_run:
				trials_repointed += sum(o.trials_count for o in others)
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

		# Pass 2: re-key every alias now that the fold above has resolved every
		# cross-sponsor collision under the hardened key. Streamed the same way as pass
		# 1 — id/sponsor_id/raw_sample/key tuples, not full instances — and bulk_update
		# is fed bare `SponsorAlias(pk=..., key=...)` stand-ins built straight from those
		# tuples rather than re-fetching full rows first.
		by_sponsor_key: dict[tuple, list] = defaultdict(list)
		alias_rows = SponsorAlias.objects.values_list(
			"id", "sponsor_id", "raw_sample", "key"
		).iterator(chunk_size=2000)
		for alias_id, sponsor_id, raw_sample, key in alias_rows:
			new_key = normalize_sponsor_key(raw_sample)
			if new_key is None:
				continue
			by_sponsor_key[(sponsor_id, new_key)].append((alias_id, key))

		to_update = []
		to_delete_ids = []
		for (_sponsor_id, new_key), rows in by_sponsor_key.items():
			rows.sort(key=lambda row: row[0])
			(survivor_id, survivor_key), *dupes = rows
			if survivor_key != new_key:
				to_update.append(SponsorAlias(pk=survivor_id, key=new_key))
			to_delete_ids.extend(alias_id for alias_id, _key in dupes)

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
