# Design note: merging clinical-trial data from multiple sources

## The problem

A single trial can be ingested by more than one source. They write to the **same**
`Trials` row, matched by a shared identifier (`nct`, `euct`, `eudract`, `ctis`) or, as a
last resort, by title. Today the outcome depends on **which importer runs last**, and the
importers don't even agree on what "update" means:

| Importer | Command | On update, blanks a field with an empty value? |
|---|---|---|
| ClinicalTrials.gov | `feedreader_trials_ctgov.py` | **No** — guarded by `if new_value and current != new_value` |
| EU CTIS | `feedreader_trials.py` (`EUTrialParser`) | **Yes** — `if field in extras …`, no truthiness guard |
| WHO ICTRP | `importWHOXML.py` | **Yes** — generic `current != value` writes even `None` |

So the real behaviour is "last write wins — **and** WHO/EU may erase a field the previous
source populated, while CT.gov never erases." Two concrete failure modes:

1. **Order-dependent results.** A trial cross-registered in EU CTIS and ClinicalTrials.gov
   has both `euct` and `nct`. Whichever importer runs last sets the shared fields
   (`title`, `condition`, `recruitment_status`, `phase`, `primary_sponsor`, …).
2. **Silent data loss.** WHO ICTRP aggregates ClinicalTrials.gov; if the WHO XML lacks a
   field that the CT.gov API had filled, importing the XML later blanks it.

Note: `identifiers` is already merged non-destructively, and many fields are produced by
**only one** source, so they never conflict (see "Conflict surface" below). The policy
below only needs to govern the genuinely shared fields.

## Goals

- **Deterministic**: same final row regardless of import order.
- **Non-destructive**: never replace a populated value with an empty one.
- **Authoritative**: prefer the best source per field.
- **Protect human edits**: a manual/admin correction should not be clobbered by an importer.
- **Auditable**: be able to tell which source last set a field.

## Conflict surface

Fields produced by a single source (no conflict — safe to keep as-is):

- **EU CTIS only**: `therapeutic_areas`, `country_status`, `trial_region`,
  `overall_decision_date`, `countries_decision_date`, `sponsor_type`
- **ClinicalTrials.gov only**: `ctg_detailed_description`, `results_url_link`
- **WHO ICTRP only**: `ethics_review_*`, `results_yes_no`, `results_ipd_plan`,
  `results_ipd_description`, `acronym`, `secondary_sponsor`, `source_support`,
  `contact_address`, `contact_affiliation`, `export_date`, `other_records`,
  `prospective_registration`, `date_enrollement`

Genuinely **shared / contested** fields (policy applies here):
`title`, `scientific_title`, `condition`, `intervention`, `primary_outcome`,
`secondary_outcome`, `primary_sponsor`, `recruitment_status`, `phase`, `study_type`,
`countries`, `inclusion_criteria`, `inclusion_agemin/agemax`, `inclusion_gender`,
`target_size`, `contact_firstname/lastname/email/tel`, `secondary_id`, `source_register`,
`published_date`, `date_registration`, `last_refreshed_on`, `results_posted`,
`results_date_completed`.

## Options

### Option A — Uniform "never blank" guard (minimal)

Apply CT.gov's guard everywhere: only overwrite when the incoming value is non-empty.
Make WHO and EU updates use `if new_value and current != new_value`.

- **Pros**: ~3 small edits, stops data loss immediately, very low risk.
- **Cons**: still last-write-wins among non-empty values — a lower-quality source can
  still clobber a better one if it runs later. Not yet deterministic.

### Option B — Source priority (+ lightweight provenance)

Define a ranking and only overwrite a field if the incoming source ranks **≥** the source
that last wrote it. Requires storing the last writer (at minimum per row, ideally per field).

Suggested ranking (highest wins):

```
manual / API edit  >  home registry  >  WHO ICTRP (aggregator)  >  generic RSS
```

where "home registry" = ClinicalTrials.gov for `nct` trials, EU CTIS for `euct`/`eudract`
trials. (A trial's canonical data is its primary registry, not the aggregator.)

- **Pros**: deterministic regardless of order; authoritative source wins; can shield
  manual edits.
- **Cons**: needs provenance storage and a priority table; "home registry" logic adds a
  branch.

### Option C — Field-level provenance + priority (most robust)

Add a JSON map on `Trials`, e.g. `field_provenance = {"phase": {"source": "ctgov",
"at": "2026-05-31T…"}, …}`. Every importer records provenance as it writes; merges consult
it.

- **Pros**: full control, fully auditable, supports per-field source-of-truth and manual-edit
  protection precisely.
- **Cons**: schema change + every importer updated; most engineering.

### Option D — Raw-per-source records + computed canonical view

Store each source's payload separately and compute the merged `Trials` representation at
read time by priority.

- **Pros**: lossless, conceptually cleanest, no destructive writes.
- **Cons**: largest refactor; the system currently assumes one `Trials` row per trial.

## Recommendation (phased)

1. **Now — Option A. ✅ Implemented.** WHO (`importWHOXML.py`) and EU
   (`feedreader_trials.py`) updates now skip writes when the incoming value is empty
   (`None`/`''`), matching CT.gov. `False`/`0` still count as real values, so e.g.
   `results_posted=False` can still be written. To avoid blanking on absence, a source
   that doesn't mention a boolean field must emit `None` (not `False`) — e.g. the EU CTIS
   parser returns `None` for `results_posted` when the "Results posted" line is missing, so
   it won't clobber a `True` set by ClinicalTrials.gov. Low risk; prerequisite for everything else.
2. **Next — Option B.** Add a single source-priority helper and a minimal provenance field
   (start row-level: a `last_source` / reuse `source_register`; upgrade to a per-field JSON
   map if needed). Encode the "home registry beats aggregator" rule so WHO can't overwrite
   CT.gov/EU primary data. Critically, mark manually-edited fields so importers skip them.
3. **Later — Option C** only if per-field source-of-truth proves necessary (e.g. you want
   `recruitment_status` always from the home registry but `summary` from whoever has the
   longest text). Option D only if the single-row model becomes a hard constraint.

## Implemented: per-registry links + stable canonical `link`

`link` was the worst-affected shared field: every registry has its own legitimate URL,
so the "never blank" guard never applied and the column flip-flopped between e.g.
`https://clinicaltrials.gov/study/NCT…` and `https://euclinicaltrials.eu/…` depending on which importer
ran last. This is now fixed:

- **`Trials.links`** (JSONField, migration 0056) stores every known URL keyed by registry
  slug, e.g. `{"ctgov": "…", "ctis": "…", "ictrp": "…"}`. Keys come from
  `gregory.utils.trial_utils.registry_from_url` (domain → slug; unknown domains fall back
  to their hostname so registries can never collide). Entries are merged with the same
  conservative semantics as `identifiers` (`merge_trial_links`): first non-empty value
  per key wins, never overwritten — so WHO ICTRP exporting an older-format registry URL
  doesn't churn against the registry's own importer.
- **`Trials.link`** is the canonical URL, governed by `canonical_link(links, current_link)`:
  **the first registry URL stored stays, chronologically.** Registries are deliberately
  NOT ranked against each other — all importers run on the same schedule, so the registry
  whose URL arrived first is where the trial team registered first, i.e. their primary
  choice. A later importer can never replace it. The one exception: a WHO ICTRP
  (aggregator) URL is upgraded once to a registry-of-record URL when one becomes
  available, since a search portal is not a registry a team registers with.
- All four write paths participate: `feedreader_trials_ctgov.py`, `feedreader_trials.py`,
  `importWHOXML.py`, and the API POST endpoint. Existing rows are backfilled by
  migration 0057 (current `link` filed under its registry key).
- `links` is exposed in the API via `TrialSerializer`.
- Tests: `gregory/tests/test_trial_links.py` (helper truth tables + importer
  integration: first-stored registry URL is kept in both import orders, re-imports
  are idempotent, aggregator upgrade).

## Implemented: per-source countries (`countries_by_source`)

`countries` is a second worst-affected shared field, for the same reason as `link`: every
source (ClinicalTrials.gov, WHO ICTRP) writes its own legitimately-different country list
into the one column, so whichever importer ran last wins, and re-importing the *other*
source's data can blank or overwrite the previous source's list. Full design:
`docs/trials-field-normalization.md` "Field: countries". Summary of the join rule
specifically:

- **`Trials.countries_by_source`** (JSONField, migration 0080) stores each source's raw
  countries string keyed by registry slug, e.g. `{"ctgov": "France, United States",
  "ictrp": "France;Iran (Islamic Republic of)"}`. `feedreader_trials_ctgov.py` writes only
  the `ctgov` key; `importWHOXML.py` writes only the `ictrp` key —
  `gregory.utils.registry_utils.merge_countries_by_source(existing_map, key, value)`
  enforces that a source can only ever touch its own key, mirroring `merge_links`. EU CTIS
  writes its own `"ctis"` key too (migration 0086, PR 2a of CTIS-API-PHASE-2-PLAN.md): the
  `feedreader_trials_ctis` retrieve-enrichment hook files the `retrieve/{ctNumber}`
  endpoint's `rowCountriesInfo` there — the only source of **non-EEA** participating
  countries for CTIS trials, since `country_status`/`countries_decision_date` are EEA-only.
- **Unlike `merge_links`'** first-value-wins semantics, `merge_countries_by_source` always
  *refreshes* the value under a source's key on re-import. There is exactly one legitimate
  value per source (not one-per-registry-URL), so there is nothing to protect by keeping a
  stale one — a source's raw country list can legitimately change between syncs (e.g. a new
  trial site added).
- The legacy `countries` column is untouched by this change and keeps its old
  last-writer-wins behaviour, documented as deprecated in favour of `countries_by_source`
  plus the normalized `TrialCountry` rows (`trial.trial_countries`).
- Tests: `gregory/tests/test_trial_countries_by_source_importers.py` (each importer writes
  only its own key; a cross-source CTGov + WHO ICTRP trial ends up with both sources' raw
  values preserved side by side, and the normalized country/region layers reflect the
  union of both).

## Derived normalized fields are immune to the flip-flop

`phase` and `recruitment_status` are shared/contested fields above — their raw vocabulary
can flip-flop between registry spellings depending on which importer wrote last.
`phase_normalized` and `recruitment_status_normalized` (see
`docs/trials-field-normalization.md`) are *derived* companion fields recomputed from their
raw counterpart on every `Trials.save()`, so each always reflects the canonical form of
whatever the raw field currently holds — it never lags behind a previous importer's value.
This is the intended pattern for any future derived field (`study_type_normalized`, ...):
compute it fresh from the raw field on every save rather than merging/preserving it across
sources.

`regions_normalized` and the `TrialCountry` rows follow the same pattern, one level up: even
though `countries_by_source` merges non-destructively (each source keeps its own key, so
there is nothing to flip-flop there), `regions_normalized` and `trial.trial_countries` are
still *recomputed fresh* from all four raw country columns on every `Trials.save()` rather
than incrementally merged/preserved — so a change to any one source's data (or the CTIS-only
`country_status`/`countries_decision_date` columns) is reflected immediately across the
whole normalized set, never partially stale.

## Open questions before implementing

- **Manual edits**: should an admin edit be permanently sticky, or only until the home
  registry reports a newer `last_refreshed_on`?
- **Home-registry tie-breaks**: for a trial with both `nct` and `euct`, which wins — and is
  that per-field or whole-row?
- **`published_date` semantics** differ by source (registration date vs study-start vs feed
  date — see `docs`/help text). Priority for this field should be decided explicitly.
- **Backfill**: do we recompute existing rows, or only apply the policy going forward?
