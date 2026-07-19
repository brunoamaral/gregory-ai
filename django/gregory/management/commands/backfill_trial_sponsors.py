"""One-time (idempotent, re-runnable) backfill of Trials.primary_sponsor_normalized and
Sponsor.sponsor_type for every trial, from the raw `primary_sponsor` / `lead_sponsor_class`
/ `sponsor_type` columns already on the row.

Trials.save() resolves primary_sponsor_normalized on every write (see
Trials._resolve_primary_sponsor() in gregory/models.py), so this command only matters for
rows written before that hook existed and for rows touched by bulk_update elsewhere
(which bypasses save() entirely — same caveat as backfill_trial_normalized_fields).

Deliberately avoids calling Trials.save() per row (29.7k saves would each re-run
sync_trial_countries(), ~3 queries per row — see docs/trials-multi-source-merge.md).
Instead:

  1. Load the full key->sponsor_id alias map into memory (bounded — a few thousand rows).
  2. Stream every trial with .only(...).iterator(chunk_size=2000), computing each raw
     primary_sponsor's key in Python.
  3. Batch-create the Sponsor + SponsorAlias rows for every key not yet in the alias map
     (first-seen raw string, in trial_id order, becomes the auto-created display name —
     same rule as the live single-row save path).
  4. bulk_update only the trial rows whose primary_sponsor_normalized actually changes.
  5. In the same pass, track the best (highest-priority) sponsor_type signal — via
     map_sponsor_type(lead_sponsor_class, sponsor_type, sponsor.name) — seen across each
     sponsor's trials, and bulk_update every non-curated sponsor whose derived type
     changes. "curated" sponsors (set by sync_sponsor_seeds) are never touched here.

See TRIALS-SPONSOR-CANONICALIZATION-PLAN.md PR 1 §6.
"""

import re
from collections import Counter

from django.core.management.base import BaseCommand
from django.utils.text import slugify

from gregory.models import Sponsor, SponsorAlias, Trials
from gregory.utils.trial_field_normalizers import map_sponsor_type, normalize_sponsor_key

_TYPE_SOURCE_PRIORITY = {"curated": 3, "ctgov": 2, "ctis": 1, "rules": 0}


def _slug_for(name: str, reserved: set[str]) -> str:
	"""In-memory equivalent of gregory.models._unique_sponsor_slug for a batch of
	not-yet-inserted sponsors: *reserved* tracks every slug already claimed in this run
	(seeded from the DB) so two new sponsors in the same batch never collide."""
	base = slugify(name)[:190] or "sponsor"
	slug = base
	n = 2
	while slug in reserved:
		suffix = f"-{n}"
		slug = f"{base[: 190 - len(suffix)]}{suffix}"
		n += 1
	reserved.add(slug)
	return slug


class Command(BaseCommand):
	help = (
		"Backfill Trials.primary_sponsor_normalized and Sponsor.sponsor_type for every "
		"trial from the raw primary_sponsor/lead_sponsor_class/sponsor_type columns."
	)

	def add_arguments(self, parser):
		parser.add_argument(
			"--batch-size",
			type=int,
			default=1000,
			help="Rows per bulk_create/bulk_update batch (default: 1000).",
		)
		parser.add_argument(
			"--dry-run",
			action="store_true",
			help="Report what would change without saving.",
		)

	def handle(self, *args, **options):
		batch_size = max(options["batch_size"], 1)
		dry_run = options["dry_run"]
		verbosity = options.get("verbosity", 1)

		alias_map: dict[str, int] = dict(SponsorAlias.objects.values_list("key", "sponsor_id"))
		sponsor_info: dict[int, dict] = {
			pk: {"name": name, "type": stype, "source": ssource}
			for pk, name, stype, ssource in Sponsor.objects.values_list(
				"pk", "name", "sponsor_type", "sponsor_type_source"
			)
		}

		# --- Pass 1: scan every trial, computing its key and (for unseen keys) the
		# first-seen display name. Rows are kept as light tuples, not full model
		# instances, so memory stays proportional to the table's row count, not its
		# column width.
		rows: list[tuple] = []
		new_key_display: dict[str, str] = {}

		queryset = Trials.objects.only(
			"trial_id",
			"primary_sponsor",
			"primary_sponsor_normalized_id",
			"lead_sponsor_class",
			"sponsor_type",
		).order_by("trial_id")

		scanned = 0
		for trial in queryset.iterator(chunk_size=2000):
			scanned += 1
			key = normalize_sponsor_key(trial.primary_sponsor)
			rows.append(
				(
					trial.trial_id,
					key,
					trial.primary_sponsor_normalized_id,
					trial.lead_sponsor_class,
					trial.sponsor_type,
				)
			)
			if key is not None and key not in alias_map and key not in new_key_display:
				display = re.sub(r"\s+", " ", trial.primary_sponsor).strip()[:500]
				if display:
					new_key_display[key] = display

		# --- Create the missing sponsors + aliases, batched. In dry-run mode nothing is
		# persisted, so fake negative ids stand in for "would be created" sponsors —
		# this keeps the resolution-counting logic in pass 2 identical for both modes.
		reserved_slugs = set(Sponsor.objects.values_list("slug", flat=True))
		new_sponsors = []
		new_aliases = []
		fake_id = 0
		for key, display in new_key_display.items():
			if dry_run:
				fake_id -= 1
				sponsor_id = fake_id
			else:
				sponsor = Sponsor(name=display, slug=_slug_for(display, reserved_slugs))
				new_sponsors.append(sponsor)
				new_aliases.append((sponsor, key, display))
				sponsor_id = None  # assigned after bulk_create below
			alias_map[key] = sponsor_id
			sponsor_info[sponsor_id] = {"name": display, "type": None, "source": None}

		if not dry_run and new_sponsors:
			Sponsor.objects.bulk_create(new_sponsors, batch_size=batch_size)
			alias_rows = []
			for sponsor, key, display in new_aliases:
				alias_map[key] = sponsor.pk
				sponsor_info[sponsor.pk] = {"name": display, "type": None, "source": None}
				alias_rows.append(SponsorAlias(sponsor=sponsor, key=key, raw_sample=display))
			SponsorAlias.objects.bulk_create(alias_rows, batch_size=batch_size)

		# --- Pass 2 (in memory, no further queries): resolve each trial's FK against the
		# now-complete alias_map, and track the best sponsor_type signal per sponsor.
		dirty_trials = []
		type_candidates: dict[int, tuple[str, str, int]] = {}
		resolved = 0
		left_null = 0

		for trial_id, key, current_fk, lead_class, sponsor_type_raw in rows:
			if key is None:
				left_null += 1
				if current_fk is not None:
					dirty_trials.append(
						Trials(trial_id=trial_id, primary_sponsor_normalized_id=None)
					)
				continue

			sponsor_id = alias_map.get(key)
			if sponsor_id is None:
				# Raw value collapsed to whitespace-only after truncation (defensive; see
				# new_key_display's `if display` guard) — treat like no sponsor.
				left_null += 1
				continue

			resolved += 1
			if current_fk != sponsor_id:
				dirty_trials.append(
					Trials(trial_id=trial_id, primary_sponsor_normalized_id=sponsor_id)
				)

			info = sponsor_info.get(sponsor_id)
			if info is None or info["source"] == "curated":
				continue
			new_type, new_source = map_sponsor_type(lead_class, sponsor_type_raw, info["name"])
			if new_type is None:
				continue
			new_priority = _TYPE_SOURCE_PRIORITY.get(new_source, -1)
			best = type_candidates.get(sponsor_id)
			baseline_priority = best[2] if best else _TYPE_SOURCE_PRIORITY.get(info["source"], -1)
			if new_priority >= baseline_priority:
				type_candidates[sponsor_id] = (new_type, new_source, new_priority)

		if not dry_run and dirty_trials:
			Trials.objects.bulk_update(
				dirty_trials, ["primary_sponsor_normalized"], batch_size=batch_size
			)

		sponsors_to_update = []
		for sponsor_id, (new_type, new_source, _priority) in type_candidates.items():
			info = sponsor_info[sponsor_id]
			if info["type"] == new_type and info["source"] == new_source:
				continue
			sponsors_to_update.append(
				Sponsor(pk=sponsor_id, sponsor_type=new_type, sponsor_type_source=new_source)
			)
		if not dry_run and sponsors_to_update:
			Sponsor.objects.bulk_update(
				sponsors_to_update, ["sponsor_type", "sponsor_type_source"], batch_size=batch_size
			)

		prefix = "Would" if dry_run else "Did"
		self.stdout.write(
			self.style.SUCCESS(
				f"Scanned {scanned} trials. {prefix} resolve {resolved} to a sponsor "
				f"({len(dirty_trials)} FK write(s)); {left_null} left with no sponsor "
				"(blank primary_sponsor)."
			)
		)
		self.stdout.write(
			f"{'Would create' if dry_run else 'Created'} {len(new_key_display)} new sponsor(s)."
		)
		type_tally = Counter(
			type_candidates[sponsor.pk][1] for sponsor in sponsors_to_update
		)
		if type_tally:
			verb = "Would set" if dry_run else "Set"
			self.stdout.write(f"{verb} sponsor_type on {len(sponsors_to_update)} sponsor(s):")
			for source, count in type_tally.most_common():
				self.stdout.write(f"  {source}: {count}")
		if verbosity >= 2 and dry_run:
			self.stdout.write("Dry run: no changes were saved.")
