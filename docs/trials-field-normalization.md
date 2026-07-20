# Design note: normalizing Trials fields

## The problem

Several `Trials` columns (`TextField`/`CharField`) store the raw registry value verbatim,
written by four separate paths:

- `feedreader_trials.py` (EU CTIS RSS, via `EUTrialParser` in `gregory/classes.py`)
- `feedreader_trials_ctgov.py` (ClinicalTrials.gov API v2)
- `importWHOXML.py` (WHO ICTRP XML export)
- API POST via `TrialSerializer`

Each registry uses its own vocabulary for the same real-world concept, so filtering or
counting against the raw column silently misses rows. Five fields are normalized so far:

- **`phase`** — the stage of testing (Phase 1–4, ...).
- **`recruitment_status`** — whether the trial is recruiting, completed, etc.
- **`study_type`** — interventional vs. observational vs. expanded access vs. basic science.
- **`countries`** (plus three sibling raw columns) — the trial's participating countries and
  derived regions.
- **`inclusion_gender`** — sex eligibility (all sexes / female-only / male-only). Unlike the
  others, the raw column here doesn't just *miss* variants — it silently returns confidently
  wrong filter results (see the field section below), so it was the first field where the
  legacy raw-text filter was removed outright rather than kept as a labeled "legacy" option.

For each, the raw column must stay untouched — it's the source-of-record value, and
rewriting it in place would break source-sync fidelity (re-importing the same registry
record should reproduce the same raw value). So normalization lives in a **derived
companion field per raw field** (`phase_normalized`, `recruitment_status_normalized`, ...),
each computed by a pure function and kept in lockstep automatically. This doc covers every
field and the shared machinery; see the per-field sections below for vocabulary and mapping
rules.

## Where the mapper lives

`django/gregory/utils/trial_field_normalizers.py`. It does **not** import
`gregory.models` — `models.py` imports from here, not the other way round, so the module
stays reusable for the next derived field without a circular import.

The module also defines the registry that drives every generic piece of machinery below:

```python
NORMALIZED_TRIAL_FIELDS = (
	("phase", "phase_normalized", normalize_phase),
	("recruitment_status", "recruitment_status_normalized", normalize_recruitment_status),
	("study_type", "study_type_normalized", normalize_study_type),
	("inclusion_gender", "inclusion_gender_normalized", normalize_inclusion_gender),
	(
		(
			"countries_by_source",
			"countries",
			"country_status",
			"countries_decision_date",
			"countries_recruitment_date",
		),
		"regions_normalized",
		_compute_regions_from_raw,
	),
)
```

Each entry is `(raw field name, derived field name, normalizer function)`. `Trials.save()`,
the backfill command, and the admin "Recompute normalized fields" action all iterate this
tuple instead of hardcoding field names — adding a new field to it (plus the matching model
field) is most of the work of extending this pattern (see the extension recipe at the
bottom).

## Field: `phase` → `phase_normalized`

### Sample raw values

A non-exhaustive sample of raw values seen in the DB for what is, semantically, the same
phase:

```
"Phase 3"            "PHASE3"           "III"              "3"
"Phase III"          "Therapeutic confirmatory  (Phase III)"
```

...and EU CTIS reports phase as a yes/no matrix across all four phases in one string:

```
Human pharmacology (Phase I): no
Therapeutic exploratory (Phase II): yes
Therapeutic confirmatory - (Phase III): no
Therapeutic use (Phase IV): no
```

### Canonical vocabulary

`gregory.utils.trial_field_normalizers.TrialPhase` (a `models.TextChoices`):

| Value | Label |
|---|---|
| `early_phase_1` | Early Phase 1 |
| `phase_1` | Phase 1 |
| `phase_1_2` | Phase 1/2 |
| `phase_2` | Phase 2 |
| `phase_2_3` | Phase 2/3 |
| `phase_3` | Phase 3 |
| `phase_3_4` | Phase 3/4 |
| `phase_4` | Phase 4 |
| `post_market` | Post-market |
| `not_applicable` | Not applicable |
| `other` | Other |

### Mapping rules

`normalize_phase(raw: str | None) -> str | None`, in order:

1. **Empty input.** `None`, `""`, or whitespace-only → `None` (phase_normalized stays
   unset — there's nothing to normalize).
2. **Clean.** Collapse every whitespace run (spaces, tabs, newlines) to a single space,
   strip, casefold. This alone fixes pretty-printed WHO/EU exports and stray double
   spaces (e.g. `"therapeutic confirmatory  (phase iii)"` → matches the table below).
3. **EudraCT/CTIS yes/no matrix.** If the cleaned string contains
   `"human pharmacology (phase i)"` followed by a colon anywhere after it, treat it as
   the matrix format and pull every `(phase <roman>): <yes|no|blank>` pair out with
   `re.findall(r"\(phase (i{1,3}|iv)\)[^:]*:\s*(yes|no)?", cleaned)` — label text in
   front of each `(phase X)` varies ("Therapeutic use (Phase IV)" vs "... - (Phase IV)")
   and is ignored. Collect the phases marked `"yes"` as a set of ints, then apply the
   **span rule** below.
4. **Exact lookup.** Look the cleaned string up in `_EXACT_MATCHES` — a flat dict
   covering every distinct raw `phase` value observed in the DB at the time this was
   built (see the module for the full table).
5. **Conservative fallback.** For formatting variants not yet in the table, extract every
   `phase <roman|digit>` token with `re.findall(r"phase\s*/?\s*(iv|i{1,3}|[0-4])\b",
   cleaned)`, convert to ints (`0` stays `0`), and apply the span rule. This is what
   catches e.g. `"Phase 3/Phase 4"` without needing an explicit table entry.
6. **Otherwise → `TrialPhase.OTHER`**, and the raw value is logged
   (`logger.info("Unmapped trial phase value: %r", raw)`) — filtering the admin on
   `phase_normalized = other` is the human review surface for anything the mapper
   doesn't yet understand.

### Span rule

Shared by steps 3 and 5. Given a set of phase ints extracted from the input:

| Set | Result |
|---|---|
| `{0}` | `early_phase_1` |
| `{1}` | `phase_1` |
| `{2}` | `phase_2` |
| `{3}` | `phase_3` |
| `{4}` | `phase_4` |
| `{1, 2}` | `phase_1_2` |
| `{2, 3}` | `phase_2_3` |
| `{3, 4}` | `phase_3_4` |
| anything else (empty, non-adjacent pairs, `0` combined with another phase, 3+ phases) | `other` |

## Field: `recruitment_status` → `recruitment_status_normalized`

Unlike `phase`, the recruitment-status vocabulary is small and closed, so there is
deliberately **no generic token fallback** — only the exact-match table below and `other`.
Guessing at a new registry spelling with a regex would be more likely to mis-map a status
than to help; new spellings should be reviewed and added to the table explicitly (same
"admin tuning workflow" as `phase`, below).

### Canonical vocabulary

`gregory.utils.trial_field_normalizers.TrialRecruitmentStatus` (a `models.TextChoices`):

| Value | Label |
|---|---|
| `not_yet_recruiting` | Not yet recruiting |
| `recruiting` | Recruiting |
| `enrolling_by_invitation` | Enrolling by invitation |
| `active_not_recruiting` | Active, not recruiting |
| `not_recruiting` | Not recruiting |
| `suspended` | Suspended |
| `completed` | Completed |
| `terminated` | Terminated |
| `withdrawn` | Withdrawn |
| `unknown` | Unknown |
| `other` | Other |

### Mapping table

Keys are the whitespace-collapsed, casefolded raw value (so `"Not Recruiting"`,
`"NOT RECRUITING"`, and `"not recruiting"` are all the same key):

| Canonical | Raw values | Source |
|---|---|---|
| `not_yet_recruiting` | `not_yet_recruiting` | ClinicalTrials.gov |
| | `authorised, recruitment pending` | EU CTIS |
| `recruiting` | `recruiting` | ClinicalTrials.gov, WHO ICTRP |
| | `ongoing, recruiting` | EU CTIS |
| | `authorised, recruiting` | EU CTIS |
| `enrolling_by_invitation` | `enrolling_by_invitation` | ClinicalTrials.gov |
| `active_not_recruiting` | `active_not_recruiting` | ClinicalTrials.gov |
| | `ongoing, recruitment ended` | EU CTIS |
| `not_recruiting` | `not recruiting` | WHO ICTRP |
| `suspended` | `suspended` | ClinicalTrials.gov |
| | `temporarily halted` | WHO ICTRP |
| | `temporarily_not_available` | ClinicalTrials.gov (expanded access) |
| `completed` | `completed` | ClinicalTrials.gov, WHO ICTRP |
| | `ended` | EU CTIS |
| `terminated` | `terminated` | ClinicalTrials.gov |
| `withdrawn` | `withdrawn` | ClinicalTrials.gov |
| `unknown` | `unknown` | ClinicalTrials.gov (not verified in >2 years) |
| | `authorised` | EUCTR/CTIS |
| | `not available` | WHO ICTRP |
| `other` | `available`, `no_longer_available`, `approved_for_marketing` | ClinicalTrials.gov (expanded-access program statuses) |

### Judgment calls

Three mappings above are deliberate, non-obvious choices rather than a straight
one-registry-value-to-one-bucket exercise:

1. **WHO's `"Not recruiting"` gets its own bucket (`not_recruiting`), not
   `active_not_recruiting`.** WHO ICTRP's generic status doesn't say whether the trial is
   pre-start, still ongoing, or fully done — folding it into `active_not_recruiting` would
   claim more precision than the source actually provides.
2. **`"Authorised"` (bare, no recruiting/pending suffix) → `unknown`, not `other`.** EUCTR
   /CTIS use it to mean "approved to run, but the feed doesn't say whether it has started
   recruiting" — that's a real "we don't know the recruitment state" case, same bucket as
   ClinicalTrials.gov's `UNKNOWN` (stale/unverified), not the same as the expanded-access
   statuses below.
3. **ClinicalTrials.gov expanded-access program statuses (`available`,
   `no_longer_available`, `approved_for_marketing`) → `other`, not `unknown` or a new
   bucket.** These describe an access *program*, not a trial's recruitment state — they
   aren't really answerable in this vocabulary at all. Left in `other` deliberately so the
   raw string (which is actually informative here) shows on display surfaces instead of a
   generic "Unknown" label.

## Field: `study_type` → `study_type_normalized`

Like `recruitment_status`, the study-type vocabulary is small and closed, so there is
deliberately **no generic token fallback** — only the exact-match table below and `other`.

### Canonical vocabulary

`gregory.utils.trial_field_normalizers.TrialStudyType` (a `models.TextChoices`):

| Value | Label |
|---|---|
| `interventional` | Interventional |
| `observational` | Observational |
| `expanded_access` | Expanded access |
| `basic_science` | Basic science |
| `other` | Other |

### Mapping table

Keys are the whitespace-collapsed, casefolded raw value. All 27 distinct raw spellings
observed in the DB (2026-07-20 audit, see STUDY-TYPE-NORMALIZATION-PLAN.md for the full
per-value count/registry inventory):

| Canonical | Raw values | Source |
|---|---|---|
| `interventional` | `INTERVENTIONAL`, `Interventional`, `interventional` | ClinicalTrials.gov, ANZCTR/NL-OMON/ISRCTN/JPRN, IRCT |
| | `Intervention` | REBEC |
| | `Interventional study` | ChiCTR |
| | `Interventional clinical trial of medicinal product` | EU CTR / EU CTIS |
| | `Treatment study` | — |
| | `BA/BE` | — |
| `observational` | `OBSERVATIONAL`, `Observational`, `observational` | ClinicalTrials.gov, DRKS |
| | `Observational study` | ChiCTR |
| | `Observational non invasive`, `Observational invasive` | NL-OMON |
| | `Diagnostic test`, `Screening` | mixed |
| | `Cause`, `Cause/Relative factors study`, `Relative factors research`, `Epidemilogical research` (sic), `Prognosis study` | — |
| `expanded_access` | `EXPANDED_ACCESS` | ClinicalTrials.gov |
| | `Expanded Access` | — |
| `basic_science` | `Basic Science`, `basic science` | — |
| `other` | `Other`, plus any unmapped value (logged) | — |

### Judgment calls

Three mappings above are deliberate, non-obvious choices:

1. **`Diagnostic test`/`Screening` → `observational`, not their own bucket.**
   ClinicalTrials.gov classifies diagnostic-accuracy studies as observational unless they
   assign an intervention, and the WHO registries that emit these strings use them as a
   study *purpose*, not an assignment model — folding them into `observational` is the
   least-wrong bucket for both.
2. **`BA/BE` → `interventional`.** Bioavailability/bioequivalence studies dose subjects by
   protocol — interventional by definition.
3. **`Basic Science` gets its own bucket, not `other`.** Only a handful of rows, but they
   are meaningfully non-clinical; folding them into `other` (the "we don't know" bucket)
   would lose that distinction — same reasoning that gave `phase`'s `post_market` its own
   bucket rather than `other`.

`Observational invasive`/`non invasive` both collapse to `observational` — the invasiveness
qualifier is NL-OMON-specific and orthogonal to study type; the raw column keeps it, the
normalized value doesn't fragment the vocabulary for one registry. `expanded_access` stays
separate from both `interventional` and `observational` — it's an access program, not a
study, consistent with how the `available`/`no_longer_available` recruitment statuses are
handled above.

Out of scope: no refetch/backfill-from-registry command exists for the ~13.1k legacy rows
with no `source_register` and therefore no `study_type` at all — this normalization only
covers what's already in the raw column.

## Field: `inclusion_gender` → `inclusion_gender_normalized`

Unlike the other three scalar fields above, the raw `inclusion_gender` filter wasn't just
weak — it was **confidently wrong**: `?inclusion_gender=Female` matched 1,772 trials via
substring search, of which only 324 were actually female-only (82% false positives, since
"Female, Male" contains "female"). This is the field where the legacy raw-text filter was
removed outright rather than kept as a labeled "legacy" option — see
INCLUSION-GENDER-NORMALIZATION-PLAN.md for the full audit.

### Canonical vocabulary

`gregory.utils.trial_field_normalizers.TrialSexEligibility` (a `models.TextChoices`):

| Value | Label |
|---|---|
| `all` | All sexes |
| `female` | Female only |
| `male` | Male only |

There is deliberately **no `other` member** — breaking symmetry with every other field in
this doc. A value literally rendered as `sex = other` would read to a clinician as
"intersex / non-binary participants", which is not what "we couldn't parse this string"
means. Unmapped values map to `None` instead, and are found via the admin/log review
workflow below (there is no `other`-filter shortcut for this field specifically).

### Mapping table

Keys are the whitespace-collapsed, casefolded raw value (25 distinct raw values collapse to
18 keys this way):

| Canonical | Raw values | Source |
|---|---|---|
| `all` | `ALL` | ClinicalTrials.gov |
| | `Both` | ChiCTR/IRCT/ISRCTN |
| | `Both males and females` | ANZCTR |
| | `Female, Male` | EU CTIS |
| | `Male and Female`, `Male/Female` | — |
| | `Female: yes Male: yes` (+ HTML-wrapped variants) | EU Clinical Trials Register |
| `female` | `Female`, `Females`, `F` | — |
| | `Female: yes Male: no` (+ HTML variant) | EU Clinical Trials Register |
| `male` | `Male`, `Males` | — |
| | `Female: no Male: yes` | EU Clinical Trials Register |
| `None` | `-`, `--`, `Not Specified` | placeholders, no eligibility signal |
| `None` (logged) | `Female: no Male: no` | contradictory registry artifact (2 rows) |

### Judgment calls

1. **No `other` bucket** (see above) — the only field in this doc without one.
2. **Placeholders → `None`, not a value.** Storing `-`/`Not Specified` as if they were data
   was the original mistake this normalization fixes.
3. **`Female: no Male: no` → `None`, but logged separately from the placeholders.**
   Logically no one could enrol — a registry data-entry artifact, not a genuine fourth
   eligibility state.
4. **No generic token fallback, and deliberately no substring match on `"female"`.** That
   substring match is precisely the bug: `"Female, Male"` contains `"female"` but is not
   female-only. `"Female, Male"` → `all` is the regression guard in the test suite.
5. **No HTML stripping in the normalizer itself.** The EU Clinical Trials Register sends
   this field as an HTML fragment (`"<br>Female: yes<br>Male: yes<br>"`); stripping happens
   once, at ingest, in WHO-HTML-CLEANUP-PLAN.md — this normalizer keeps the HTML-wrapped
   keys in its exact-match table anyway (expected-dead post-cleanup, but free, and a safety
   net for a stale re-import of cached XML).

### API

The legacy `inclusion_gender` (`icontains`) filter is **removed**, not deprecated — see the
judgment call above. `inclusion_gender_normalized` (`ChoiceFilter`) replaces it. The raw
`inclusion_gender` field stays on the serializer (source-of-record value, unaffected). A
request using the removed `?inclusion_gender=...` parameter is silently ignored by
django-filter (unknown params don't error) rather than rejected, so old clients get
unfiltered results, not a 400 — called out explicitly as a breaking change in
docs/03-api-and-rss-feeds.md. `/trials/stats/` gets a `by_sex` facet (per
`TrialSexEligibility` + `no_sex_data`; `no_sex_data` is ~46% of trials globally, same
legacy-`source_register`-null caveat as `no_modality`/`no_study_type`).

## Field: `countries` → `TrialCountry` rows + `regions_normalized` (multi-input)

Unlike `phase` and `recruitment_status`, country data is spread across **four** raw columns
(`countries_by_source`, the legacy `countries`, `country_status`, `countries_decision_date`)
written by three different sources with three different formats, and the canonical output
isn't a single scalar — it's a set of per-country rows plus a derived region list. Summary:

- **`Trials.countries_by_source`** (JSONField) fixes last-write-wins on the legacy
  `countries` column: each importer writes only its own key (`ctgov`/`ictrp`/`ctis`,
  reusing `registry_utils.REGISTRY_DOMAINS` slugs) via
  `registry_utils.merge_countries_by_source`, mirroring the `links`/`merge_links` pattern
  but *refreshing* the value on every re-import rather than keeping the first-seen one (a
  source's own country list can legitimately change between syncs). EU CTIS writes its own
  `"ctis"` key too, sourced from the `retrieve/{ctNumber}` endpoint's `rowCountriesInfo`
  (semicolon-joined display names — see `feedreader_trials_ctis`'s `_enrich_from_retrieve`
  hook, CTIS-API-PHASE-2-PLAN.md) — this is the only source of **non-EEA** participating
  countries for CTIS trials; `country_status`/`countries_decision_date` remain EEA-only.
- **`Trials.countries_recruitment_date`** (JSONField) mirrors `countries_decision_date`:
  per-country recruitment start dates keyed by ISO alpha-2 code, sourced from the retrieve
  endpoint's `authorizedPartsII[].mscInfo.trialRecruitmentPeriod` (earliest date when a
  country reports more than one period). CTIS-only, replaced wholesale on every enrichment
  run (deterministic, idempotent) rather than merged.
- **`gregory.utils.trial_field_normalizers.normalize_countries(countries_by_source,
  countries, country_status, countries_decision_date, countries_recruitment_date)`**
  computes the union of every input into `[{"country": "DE", "status": "recruiting",
  "status_raw": "...", "decision_date": "2024-07-19",
  "recruitment_start_date": "2024-08-01", "sources": ["ctgov", "ctis"]}, ...]`, sorted by
  country code, `None` when every input is empty. `countries_recruitment_date` is optional
  (defaults to `None`) for backward compatibility with existing call sites. See the module
  for the full tokenizer/alias-table design (typos, UK subdivisions, UN-style names, region
  literals).
- **`TrialCountry`** (`gregory/models.py`) is a through model (`trial`, `country`
  (`CountryField`), `status`/`status_raw`/`decision_date`/`recruitment_start_date`
  (CTIS-only, null otherwise), `sources`) holding one row per `normalize_countries()`
  result. `Trials.sync_trial_countries()` replaces the full set after every
  `Trials.save()`; the backfill command and admin "Recompute normalized fields" action call
  it explicitly for `bulk_update` paths.
- **`gregory.utils.trial_field_normalizers.normalize_regions(country_codes,
  raw_countries)`** reduces a list of country codes (plus a secondary scan of the raw
  `countries` text for literal region/continent tokens like `"Europe"` or
  `"Asia(except Japan)"`) to a sorted list of region slugs — `africa`, `asia`, `europe`,
  `north_america`, `south_america`, `oceania`. `Trials.regions_normalized` is registered in
  `NORMALIZED_TRIAL_FIELDS` via a private glue function (`_compute_regions_from_raw`) that
  calls `normalize_countries()` then `normalize_regions()`, so it participates in the
  standard save()/backfill/admin machinery like `phase_normalized`.
- API: `TrialSerializer` exposes `trial_countries` (nested: country, status, decision_date,
  recruitment_start_date, sources), `countries_normalized` (flat code list), and
  `regions_normalized`; `?country=DE` and `?region=europe` filters in `api/filters.py`. The
  legacy `countries` field is unchanged.

## The save() guarantee

`Trials.save()` (in `django/gregory/models.py`) recomputes every derived field registered
in `NORMALIZED_TRIAL_FIELDS` from its raw counterpart(s) on every write. Since
`regions_normalized` (see "Field: countries" below) derives from **four** raw columns
rather than one, the raw-field slot in each `NORMALIZED_TRIAL_FIELDS` entry accepts either
a single field name (str) or a tuple of field names; `raw_field_names()` normalizes either
shape to a tuple so the loop is agnostic to which kind of entry it's looking at:

```python
def save(self, *args, **kwargs):
	update_fields = kwargs.get("update_fields")
	extra_update_fields = []
	for raw_fields, derived_field, normalizer in NORMALIZED_TRIAL_FIELDS:
		names = raw_field_names(raw_fields)
		setattr(self, derived_field, normalizer(*(getattr(self, name) for name in names)))
		if (
			update_fields is not None
			and any(name in update_fields for name in names)
			and derived_field not in update_fields
		):
			extra_update_fields.append(derived_field)
	if extra_update_fields:
		kwargs["update_fields"] = [*update_fields, *extra_update_fields]
	super().save(*args, **kwargs)
	self.sync_trial_countries()
```

This covers all four write paths — they all go through `.objects.create()` or `.save()`,
including e.g. `save(update_fields=["phase"])`, which the code above extends to also
persist `phase_normalized` (and, symmetrically, `update_fields=["recruitment_status"]`
extends to persist `recruitment_status_normalized`, and `update_fields=["countries"]`
extends to persist `regions_normalized`, since `"countries"` is one of its four raw
fields). Every derived field is `editable=False`, so none can be set directly through a
form or API POST — they are always derived.

**`bulk_update()` bypasses `save()`** and therefore bypasses this hook. The only place
that matters today is the backfill command below and the admin "Recompute normalized
fields" action, both of which recompute the derived fields explicitly before calling
`bulk_update`.

The derived fields are plain fields on `Trials`, so django-simple-history
(`HistoricalRecords`) picks them up automatically — no extra wiring needed.

`sync_trial_countries()` — the `TrialCountry` replace-sync described below — is a separate
step run *after* `super().save()` (it needs a pk for the related rows) and is **not** part
of the `NORMALIZED_TRIAL_FIELDS` loop, since it replaces related-model rows rather than
setting a scalar field. `bulk_update()` bypasses it exactly like the scalar derived fields;
the backfill command and admin action call it explicitly.

## Admin tuning workflow

When registries introduce a new spelling the mapper doesn't recognise, it lands in
`other`:

1. In `/admin/gregory/trials/`, filter **Phase normalized** = `Other` (or **Recruitment
   status normalized** = `Other`).
2. Eyeball the raw values on those rows (or check `logger.info` output / the backfill
   command's OTHER report — see below).
3. Add the new raw value(s) to `_EXACT_MATCHES` (phase) or
   `_RECRUITMENT_STATUS_EXACT_MATCHES` (recruitment status) in
   `gregory/utils/trial_field_normalizers.py`. For phase, prefer this over loosening the
   generic fallback regex, which is meant to stay conservative; recruitment status has no
   fallback at all, so this table is the only way to widen its coverage.
4. Deploy the change.
5. Back in the admin, select the affected rows (still filtered to `Other`, or select all)
   and run the **"Recompute normalized fields"** action — it re-derives every registered
   normalized field for the selected queryset immediately, without waiting for the next
   registry sync to re-save each row.

## Backfill command

```
python manage.py backfill_trial_normalized_fields [--field phase] [--field recruitment_status] [--field study_type] [--batch-size 1000] [--dry-run]
```

`--field` is repeatable and/or comma-separated (`--field phase,recruitment_status`);
omitting it backfills every field registered in `NORMALIZED_TRIAL_FIELDS`. Selector names
are the derived field name with `_normalized` dropped — `phase`, `recruitment_status`,
`study_type`, and `regions` (the countries/`TrialCountry` layer). Scans every `Trials` row once, recomputes
the selected derived field(s), and `bulk_update`s the rows whose stored scalar value(s)
differ, flushing every `--batch-size` dirty rows during the scan so peak memory stays
bounded by the batch rather than the table (on the first run every row is dirty). When
`regions` is selected, also calls `sync_trial_countries()` for every scanned trial
(regardless of whether `regions_normalized` itself changed — a trial can need fresh
`TrialCountry` rows on the very first run even when its computed regions happen to already
match). Intentionally skips django-simple-history — these
are derived fields recomputed from data already in the row, not a meaningful edit, and
`bulk_update` can't write history anyway (it doesn't call `save()`). Reports total scanned,
total values changed, a per-field per-canonical-value tally, and (per field) every distinct
raw value that mapped to `other` — that list is the review queue for step 2 of the tuning
workflow above. Idempotent: rerunning after the DB is caught up updates nothing.

## Prod runbook

1. Deploy this change (model + migrations
   `0078_historicaltrials_phase_normalized_and_more` and
   `0079_historicaltrials_recruitment_status_normalized_and_more`).
2. `docker exec gregory python manage.py migrate`
3. One-time: `docker exec gregory python manage.py backfill_trial_normalized_fields`
   (backfills both `phase_normalized` and `recruitment_status_normalized` in one pass).
4. Optional: review the OTHER list(s) the backfill prints, extend the relevant mapping
   table, deploy, rerun the backfill (or use the admin action) to pick up the new mappings.

`GET /trials/stats/` (`TrialViewSet.build_stats_payload`) aggregates on
`recruitment_status_normalized`, so its counts are only meaningful **after** step 3 runs.
Before the backfill, every existing row has `recruitment_status_normalized = NULL` (the
column doesn't get a value until `Trials.save()` or the backfill computes it), so the
entire pre-backfill trial set shows up in the `no_status` bucket rather than
`recruiting`/`completed`/etc. — expect `no_status` to hold the bulk of `total` immediately
after migrating, until the backfill catches the table up.

`study_type_normalized` (migration `0089_historicaltrials_study_type_normalized_and_more`)
follows the same pattern: `docker exec gregory python manage.py migrate`, then
`docker exec gregory python manage.py backfill_trial_normalized_fields --field study_type
--dry-run` to review the OTHER report before running it for real. Expect `no_study_type` to
hold ~13.2k trials even after the backfill catches up — that's mostly legacy rows with no
`source_register` at all, not a normalization gap (see the `study_type` section above).

`inclusion_gender_normalized` (migration
`0092_historicaltrials_inclusion_gender_normalized_and_more`) follows the same pattern:
`docker exec gregory python manage.py migrate`, then `docker exec gregory python manage.py
backfill_trial_normalized_fields --field inclusion_gender --dry-run` — review the unmapped
report (this field has no `other` bucket, so the report is driven by
`logger.info`/backfill-tally output rather than an admin `= Other` filter), then run for
real. Sanity check on prod: `?inclusion_gender_normalized=female` should return roughly
**324** trials globally (vs. 1,772 from the removed legacy substring filter) — that gap *is*
the bug this field fixes. `by_sex.all` should be ≈ 14,700; expect `no_sex_data` to hold
~46% of trials (same legacy-`source_register`-null population as `no_study_type`).

## Extension recipe

This design is meant to repeat directly for the next raw field with a messy multi-registry
vocabulary — `phase`, `recruitment_status`, `study_type`, and `countries`/`regions_normalized`
all followed it:

1. In `gregory/utils/trial_field_normalizers.py`, add a new `models.TextChoices`, a new
   pure function, and add a new `(raw_field, derived_field, normalizer)` entry to
   `NORMALIZED_TRIAL_FIELDS`. Same module — it's named `trial_field_normalizers`, not
   `trial_phase_normalizer`, for exactly this reason.
2. In `models.py`, add the derived field next to its raw counterpart (`CharField`,
   `choices=`, `db_index=True`, `editable=False`). `Trials.save()` needs **no changes** —
   it already iterates `NORMALIZED_TRIAL_FIELDS`, so the new entry is picked up
   automatically, including the `update_fields` shim.
3. `makemigrations gregory`.
4. No new backfill command needed —
   `backfill_trial_normalized_fields` already covers every registered field; the new one
   is included by default, or scoped alone with `--field <name>`.
5. Wire up the API filter/serializer/CSV/XLSX columns and the admin
   list_filter/readonly_fields/fieldset entries the same way as `phase`/`phase_normalized`
   above. The admin "Recompute normalized fields" action needs no changes — it also
   iterates `NORMALIZED_TRIAL_FIELDS`.
6. Test coverage mirrors `gregory/tests/test_trial_phase_normalization.py` /
   `gregory/tests/test_trial_recruitment_status_normalization.py` /
   `gregory/tests/test_trial_study_type_normalization.py`.

## Cross-reference: multi-source merge

See `docs/trials-multi-source-merge.md` — `phase` and `recruitment_status` are
"genuinely shared / contested" fields where different importers can flip-flop the raw value
depending on import order. Their `_normalized` companions are immune to that: because each
is recomputed from whatever the raw field currently holds on every save, it always reflects
the current raw value's canonical form, never a stale one from a previous importer.

## Tests

- `gregory/tests/test_trial_phase_normalization.py` — parametrized coverage of every raw
  value in `_EXACT_MATCHES`, the EudraCT matrix (all phase combinations, blank cells, label
  variants), the generic fallback, unmapped values, the `Trials.save()` hook (including
  `update_fields`), the generalized backfill command (including `--field`, `--dry-run`, and
  idempotency), and the API filter (`?phase_normalized=phase_3`, invalid choice, response
  body).
- `gregory/tests/test_trial_recruitment_status_normalization.py` — mirrors the above for
  `recruitment_status`: every raw value in `_RECRUITMENT_STATUS_EXACT_MATCHES` (including
  original casings), `None`/empty/whitespace → `None`, unmapped → `other` + logged, the
  `Trials.save()` hook, backfill coverage (including a `--field phase` run leaving
  `recruitment_status_normalized` untouched), and the API filter/serializer tests.
- The admin "Recompute normalized fields" action has a smoke test alongside the other admin
  tests in `gregory/tests/test_trial_phase_normalization.py`.
- `gregory/tests/test_trial_study_type_normalization.py` / `api/tests/test_trial_study_type_normalization.py`
  — mirror the recruitment_status coverage above for `study_type`: every raw value in
  `_STUDY_TYPE_EXACT_MATCHES`, `None`/empty/whitespace → `None`, unmapped → `other` +
  logged, no generic token fallback, the `Trials.save()` hook, backfill coverage
  (`--field study_type`, `--dry-run`, idempotency, scoping), the admin recompute action,
  and the API filter/serializer tests (`?study_type_normalized=interventional`, invalid
  choice, response body, legacy `study_type` `icontains` filter unaffected). The
  `by_study_type` facet on `/trials/stats/` is covered in
  `api/tests/test_trials_stats.py::TrialStatsStudyTypeFacetTest`.
- `gregory/tests/test_trial_country_normalization.py` — `normalize_countries`/
  `normalize_regions` coverage (CTGov comma lists, WHO semicolon lists with duplicates,
  `Korea, Republic of` stored alone, `country_status` comma-containing-status parsing,
  mixed-provenance union, non-ISO decision-date keys, legacy `countries` format-detection
  fallback, typos/UK-subdivisions/region-literal handling), `registry_utils
  .merge_countries_by_source`, the `Trials.save()` hook for `regions_normalized` +
  `TrialCountry` sync (including `update_fields=["countries"]`), the backfill command's
  `regions` selector (including `TrialCountry` rebuild and its `--dry-run` behaviour), and
  the admin recompute action.
- `gregory/tests/test_trial_countries_by_source_importers.py` — importer integration:
  `feedreader_trials_ctgov.py`/`importWHOXML.py` each write only their own
  `countries_by_source` key, never clobbering the other source's key, including a
  cross-source (CTGov + WHO) union test that also checks the resulting
  `regions_normalized`.
- `api/tests/test_trial_country_normalization.py` — `?country=DE`/`?region=europe` filters
  (including invalid-choice 400 and case-insensitivity), the `trial_countries`/
  `countries_normalized`/`regions_normalized` response fields, `regions_normalized`'s
  read-only serializer field, and a bounded-query-count check for the list endpoint
  (`trial_countries` prefetch).
- `gregory/tests/test_trial_inclusion_gender_normalization.py` /
  `api/tests/test_trial_inclusion_gender_normalization.py` — mirror the `study_type`
  coverage above for `inclusion_gender`: every raw value in
  `_INCLUSION_GENDER_EXACT_MATCHES` (including the HTML-wrapped EU Clinical Trials Register
  variants), the `"Female, Male"` → `all` regression guard (not `female`), the contradictory
  `"Female: no Male: no"` → `None` + logged case, placeholders → `None`,
  `None`/empty/whitespace → `None`, an unseen spelling → `None` + logged, no generic token
  fallback, the `Trials.save()` hook, backfill coverage (`--field inclusion_gender`,
  `--dry-run`, idempotency, scoping), the admin recompute action, the removed legacy
  `?inclusion_gender=` filter (now silently unfiltered, not 400), and the
  `?inclusion_gender_normalized=` filter/serializer tests. The `by_sex` facet on
  `/trials/stats/` is covered in
  `api/tests/test_trials_stats.py::TrialStatsSexFacetTest`.
