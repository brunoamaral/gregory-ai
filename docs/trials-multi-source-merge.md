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
   `results_posted=False` can still be written. Low risk; prerequisite for everything else.
2. **Next — Option B.** Add a single source-priority helper and a minimal provenance field
   (start row-level: a `last_source` / reuse `source_register`; upgrade to a per-field JSON
   map if needed). Encode the "home registry beats aggregator" rule so WHO can't overwrite
   CT.gov/EU primary data. Critically, mark manually-edited fields so importers skip them.
3. **Later — Option C** only if per-field source-of-truth proves necessary (e.g. you want
   `recruitment_status` always from the home registry but `summary` from whoever has the
   longest text). Option D only if the single-row model becomes a hard constraint.

## Open questions before implementing

- **Manual edits**: should an admin edit be permanently sticky, or only until the home
  registry reports a newer `last_refreshed_on`?
- **Home-registry tie-breaks**: for a trial with both `nct` and `euct`, which wins — and is
  that per-field or whole-row?
- **`published_date` semantics** differ by source (registration date vs study-start vs feed
  date — see `docs`/help text). Priority for this field should be decided explicitly.
- **Backfill**: do we recompute existing rows, or only apply the policy going forward?
