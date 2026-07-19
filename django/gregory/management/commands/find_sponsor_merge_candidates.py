"""Generate SponsorMergeCandidate rows for human review (PR D2).

Idempotent, safe to re-run any time (e.g. monthly, or after a big import): only
creates *new* pending candidates. Never touches an existing row in any status — a
dismissed pair must never reappear (see SPONSOR-SURFACE-PLAN.md PR D2).

Two detection bases, both built on top of normalize_sponsor_key()'s hardened,
punctuation-insensitive output:

  suffix_variant: strip trailing legal-entity suffix tokens (Inc, Ltd, GmbH, AG, ...)
  repeatedly from the end of the hardened key (never mid-name) and group sponsors that
  collide on the stripped key. Each group's largest member (by trial count, ties to
  lowest id) is the target every other member is paired against. Deliberately never
  auto-merges: "Merck" + "Merck AB" + "Merck KGaA" fold into one group here even
  though bare "Merck" is ambiguous between the MSD and KGaA families — the whole point
  of the review queue.

  containment: hardened key X is a strict token-prefix of key Y (X has >= 2 tokens),
  with the "of"-continuation noise filter — the immediate next token after X must not
  be "of" — which removes the bulk of "University" subset-of "University of ..."
  pairs (different-name continuations, not variants) while keeping genuine
  subsidiary/campus-style pairs like "Aalborg University" / "Aalborg University
  Hospital".

Usage:
    python manage.py find_sponsor_merge_candidates
"""

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db.models import Count

from gregory.models import Sponsor, SponsorMergeCandidate, SponsorMergeCandidateBasis
from gregory.utils.trial_field_normalizers import normalize_sponsor_key

_LEGAL_SUFFIXES = {
	"inc", "incorporated", "ltd", "limited", "llc", "llp", "plc", "gmbh", "ag", "sa",
	"srl", "bv", "nv", "as", "ab", "aps", "oy", "kk", "co", "corp", "corporation",
	"company", "pty", "pvt", "sarl", "spa", "kgaa", "lp",
}

_END = "$end$"


def _strip_legal_suffixes(key: str) -> str:
	tokens = key.split(" ")
	while tokens and tokens[-1] in _LEGAL_SUFFIXES:
		tokens.pop()
	return " ".join(tokens) or key


def _ordered_pair_ids(a: Sponsor, b: Sponsor) -> tuple[int, int]:
	return (a.pk, b.pk) if a.pk < b.pk else (b.pk, a.pk)


def _suffix_variant_candidates(keyed, existing_pairs):
	groups = defaultdict(list)
	for sponsor, key in keyed:
		groups[_strip_legal_suffixes(key)].append(sponsor)

	candidates = []
	for stripped_key, members in groups.items():
		if len(members) < 2:
			continue
		target = max(members, key=lambda s: (s.trials_count, -s.pk))
		for sponsor in members:
			if sponsor.pk == target.pk:
				continue
			a_id, b_id = _ordered_pair_ids(target, sponsor)
			if (a_id, b_id) in existing_pairs:
				continue
			existing_pairs.add((a_id, b_id))
			candidates.append((a_id, b_id, SponsorMergeCandidateBasis.SUFFIX_VARIANT, stripped_key))
	return candidates


def _containment_candidates(keyed, existing_pairs):
	# Trie over key tokens: each node maps a token to its child node, plus an
	# _END bucket holding the sponsors whose key ends exactly at that node.
	root: dict = {}
	for sponsor, key in keyed:
		node = root
		for token in key.split(" "):
			node = node.setdefault(token, {})
		node.setdefault(_END, []).append(sponsor)

	candidates = []
	for sponsor, key in keyed:
		tokens = key.split(" ")
		if len(tokens) < 2:
			continue
		node = root
		for token in tokens:
			node = node.get(token)
			if node is None:
				break
		if node is None:
			continue

		stack = [(child_token, child_node) for child_token, child_node in node.items()]
		while stack:
			child_token, child_node = stack.pop()
			if child_token == _END:
				continue
			if child_token == "of":
				continue  # noise filter: "X" subset of "X of ..." continuations
			for other in child_node.get(_END, []):
				if other.pk == sponsor.pk:
					continue
				a_id, b_id = _ordered_pair_ids(sponsor, other)
				if (a_id, b_id) in existing_pairs:
					continue
				existing_pairs.add((a_id, b_id))
				candidates.append((a_id, b_id, SponsorMergeCandidateBasis.CONTAINMENT, key))
			for grandchild_token, grandchild_node in child_node.items():
				if grandchild_token != _END:
					stack.append((grandchild_token, grandchild_node))
	return candidates


class Command(BaseCommand):
	help = "Generate SponsorMergeCandidate rows (suffix_variant + containment) for admin review."

	def handle(self, *args, **options):
		sponsors = list(
			Sponsor.objects.annotate(trials_count=Count("trials", distinct=True))
		)
		keyed = []
		for sponsor in sponsors:
			key = normalize_sponsor_key(sponsor.name)
			if key is not None:
				keyed.append((sponsor, key))

		existing_pairs = set(
			SponsorMergeCandidate.objects.values_list("sponsor_a_id", "sponsor_b_id")
		)
		sponsors_by_id = {s.pk: s for s, _key in keyed}

		suffix_rows = _suffix_variant_candidates(keyed, existing_pairs)
		containment_rows = _containment_candidates(keyed, existing_pairs)

		to_create = [
			SponsorMergeCandidate(
				sponsor_a=sponsors_by_id[a_id],
				sponsor_b=sponsors_by_id[b_id],
				basis=basis,
				shared_key=shared_key,
			)
			for a_id, b_id, basis, shared_key in suffix_rows + containment_rows
		]
		SponsorMergeCandidate.objects.bulk_create(to_create)

		self.stdout.write(
			self.style.SUCCESS(
				f"{len(suffix_rows)} suffix_variant candidate(s), "
				f"{len(containment_rows)} containment candidate(s) created."
			)
		)
