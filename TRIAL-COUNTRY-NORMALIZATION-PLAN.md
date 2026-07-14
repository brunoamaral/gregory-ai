# Trial country normalization: source audit and plan

Normalize trial country data following the pattern established for `phase_normalized` and
`recruitment_status_normalized` (see `docs/trials-field-normalization.md`).

**Status: implemented on `claude/trial-country-normalization-impl`.** This document is the
audit + design record; see that doc plus `docs/trials-multi-source-merge.md` for the
day-to-day reference once merged. The prod one-time backfill (`backfill_trial_normalized_fields`)
has **not** been run yet — see the runbook note at the bottom.

## Background: how each source reports country

- **ClinicalTrials.gov** (`feedreader_trials_ctgov`, runs in pipeline):
  `ClinicalTrialsGovAPI.parse_study_to_clinical_trial` in `gregory/classes.py` extracts site
  locations, dedupes, sorts, joins with `", "` into the `countries` column. CTGov v2 display
  names (`United States`, `South Korea`, `Czechia`, `Turkey (Türkiye)`). Semantics: site
  locations. Trials with no locations → `countries = None`.
- **WHO ICTRP** (`importWHOXML`, manual command): reads `<Countries>` verbatim into
  `countries`. Semicolon-joined (`France;Finland;Spain;Germany`), sometimes trailing `;`.
  UN-style names with internal commas (`Iran (Islamic Republic of)`, `Korea, Republic of`).
  Has duplicates within one value (83 rows). Junk: typos (`Chian`, `Modalvia`,
  `Bosnial and Herzegovina`, `United Kindgdom`, `Italia`), UK subdivisions (`England`,
  `Scotland`, `Wales`, `Northern Ireland`), continents/regions (`Europe`, `North America`,
  `Asia(except Japan)`, `Oceania`, `European Union`), casing variants (`CHINA`, `china`),
  literals `none`/`Other`. Semantics: recruitment countries.
- **EU CTIS** (`feedreader_trials`, RSS, runs in pipeline): `EUTrialParser.parse_summary` in
  `gregory/classes.py`. Never populates `countries`. Country data lives in:
  `country_status` (TextField, e.g. `"Spain:Authorised, recruitment pending, France:Authorised,
  recruitment pending, Italy:Ongoing, recruiting"` — parse by matching `Name:` prefix, NOT by
  splitting on commas); `countries_decision_date` (JSONField, already ISO alpha-2:
  `{"DE": "2024-07-19", "ES": "2024-07-22"}`); `trial_region` (CharField, only `EEA only` /
  `In both EEA and non-EEA`). Semantics: regulatory decisions per member state.

**Cross-source hazard**: all three importers overwrite the single `countries` column
(last-writer-wins), so `source_register` is NOT reliable provenance — format must be
detected from the value itself.

## Layer 1 — fix last-write-wins (`countries_by_source`)

`Trials.countries_by_source` (JSONField), mirroring the `links`/`registry_utils.merge_links`
pattern (NOT the `identifiers` string-merge — two differently-delimited strings cannot be
merged in place). Each importer writes ONLY its own key:

- `{"ctgov": "France, United States", "ictrp": "France;Iran (Islamic Republic of)"}`. Keys
  reuse registry slugs from `registry_utils.REGISTRY_DOMAINS` (`ctgov`, `ictrp`).
- `feedreader_trials_ctgov` writes `ctgov`; `importWHOXML` writes `ictrp`. Values verbatim
  per source. Unlike `merge_links`' first-value-wins semantics, a source's own key is always
  refreshed (`registry_utils.merge_countries_by_source`) — its raw country list can
  legitimately change between syncs.
- EU CTIS needs no key (its data already lives in its own columns).
- Legacy `countries` column keeps current last-writer-wins behaviour for API compatibility;
  documented as deprecated.
- The backfill command seeds the map from existing `countries` by format detection
  (`;` → `ictrp`, else `ctgov`) — this fallback is also built into `normalize_countries`
  itself, so it applies automatically on every save until `countries_by_source` is seeded.

## Layer 2 — normalized countries (`TrialCountry` through model)

```python
class TrialCountry(models.Model):
	trial = models.ForeignKey(Trials, related_name="trial_countries", on_delete=models.CASCADE)
	country = CountryField()  # django_countries, ISO 3166-1 alpha-2
	status = models.CharField(choices=TrialRecruitmentStatus.choices, null=True)  # from CTIS country_status
	status_raw = models.CharField(null=True)
	decision_date = models.DateField(null=True)  # from countries_decision_date
	sources = models.JSONField(default=list)  # e.g. ["ctgov", "ictrp", "ctis"]

	class Meta:
		constraints = [models.UniqueConstraint(fields=["trial", "country"], name="unique_trial_country")]
```

- `country` uses `django_countries` (already a dependency, v7.6.1, used by `Authors.country`).
- Per-country `status` reuses `normalize_recruitment_status` (CTIS `country_status` values
  are the same vocabulary).
- `decision_date` from `countries_decision_date` (CTIS-only, null for CTGov/WHO countries).
- `sources` records which slug(s) contributed.
- Sync runs after `super().save()` in `Trials.save()` (`Trials.sync_trial_countries()` — M2M
  rows need a pk): recompute full row set from raw columns and replace. `bulk_update` paths
  bypass `save()` — the backfill command and admin recompute action call
  `sync_trial_countries()` explicitly.
- API perf: `TrialSerializer` prefetches `trial_countries` (no per-row queries on list
  endpoints) via `TrialViewSet.get_queryset()` / `TrialSearchView.get_queryset()`.

## Layer 3 — regions (`regions_normalized` JSONField)

Sorted list of region slugs, choices: `africa`, `asia`, `europe`, `north_america`,
`south_america`, `oceania`.

- Primary: map each normalized country code to its region via a static alpha-2 → region
  table in `trial_field_normalizers.py` (`_COUNTRY_TO_REGION`; UN M49-ish continental
  grouping; Central America/Caribbean → `north_america`).
- Secondary: literal region tokens found in raw `countries` (`Europe`, `Asia(except Japan)`,
  `North America`, `Oceania`, `South America`, `European Union` → `europe`) union in.
- Computed after the country layer (`_compute_regions_from_raw` in
  `trial_field_normalizers.py` composes `normalize_countries` + `normalize_regions`).

## Normalizer design (`gregory/utils/trial_field_normalizers.py`)

Pure functions:

```python
def normalize_countries(countries_by_source, countries, country_status,
                        countries_decision_date) -> list[dict] | None
	# → [{"country": "DE", "status": "recruiting", "status_raw": "...",
	#     "decision_date": "2024-07-19", "sources": ["ctgov", "ctis"]}, ...]

def normalize_regions(country_codes, raw_countries) -> list[str] | None
```

These derive from MULTIPLE raw inputs, so they don't fit the `(raw, derived, fn)` shape of
the original `NORMALIZED_TRIAL_FIELDS`. The registry tuple's raw-field slot now accepts a
tuple of raw field names (`raw_field_names()` normalizes either shape to a tuple) — preferred
over special-casing in `save()`. `study_type` (next in line) stays single-input, but the
machinery is now general.

Computation is the UNION across inputs:

1. `countries_decision_date` keys — already alpha-2; validated against the ISO list, taken
   as-is, attach date, source `ctis`. Non-ISO keys are logged and dropped (defensive).
2. `countries_by_source` — tokenize each key's value, tagging with that key as source. Falls
   back to the legacy `countries` column (format-detected) for rows not yet seeded.
3. `country_status` — extract names by matching `Name:` prefix (regex
   `([A-Za-z][A-Za-z ]*):` at segment starts), NOT comma-split; attach per-country status
   (raw + `normalize_recruitment_status`), source `ctis`.

Tokenizer per value:

- If whole value matches a known country name → single country (protects `Korea, Republic
  of` stored alone).
- Else if `;` present → split on `;` (WHO).
- Else → split on comma (tolerates both `", "` and a bare `,` with no following space — the
  latter covers compact multi-token values like `Japan,Asia(except Japan),Europe`), then
  re-join adjacent fragments that only match a known name when combined.

Token mapping, in order:

1. Whitespace-collapse + casefold, strip trailing punctuation (handles `CHINA`, `china`,
   trailing `;`).
2. Exact alias table (`_COUNTRY_EXACT_MATCHES`), seeded from the inventory below: UN-style
   names, old names (`Czech Republic`→`CZ`, ...), typos, `US`/`USA`→`US`, UK
   subdivisions→`GB`, `The Netherlands`→`NL`, etc.
3. Region tokens (`_REGION_TOKENS`) route to `normalize_regions` instead of the country list.
4. Fallback: `django_countries` name lookup (`_name_to_code_lookup()`, lazily built from
   `django_countries.data.COUNTRIES`).
5. Unmapped tokens: logged (`Unmapped trial country value: ...`) and dropped. Raw columns
   keep everything.
6. Result: sorted, deduped set; `None`/no rows when all inputs empty.

Token inventory seeded into the alias table (count, token → code):

```
5532 United States / 38 United States of America / 1 US   → US
1421 United Kingdom / 63 England / 17 Scotland / 15 Wales / 6 Northern Ireland → GB
 613 Turkey (Türkiye) / 108 Turkey / 5 Türkiye  → TR
 617 Iran (Islamic Republic of) / 50 Iran  → IR
 435 Czechia / 218 Czech Republic  → CZ
 380 South Korea / 27 Korea, Republic of / 5 Republic of Korea  → KR
 275 Russia / 173 Russian Federation  → RU
1043 Netherlands / 29 The Netherlands  → NL
1380 China / 2 CHINA / 5 china / 1 People's Republic of China / 1 Chian  → CN
```

One-off noise mapped or dropped: `Modalvia`(→MD, Moldova typo), `Bosnial and
Herzegovina`(→BA), `United Kindgdom`(→GB), `Italia`(→IT), `Former Serbia and Montenegro`
(dropped — dissolved 2006, no clean current ISO code), `none`/`Other` (dropped). Region
tokens: `Europe`, `North America`, `South America`, `Oceania`, `Asia(except Japan)`,
`European Union` (→`europe`); a defensive bare `Asia`/`Africa` pair was also added beyond the
observed inventory.

## Rollout steps

1. `normalize_countries` + `normalize_regions` + alias/region tables + unit tests in
   `django/gregory/tests/test_trial_country_normalization.py` (fixtures cover the
   `Korea, Republic of` single-value case, WHO duplicates, CTIS `country_status` parsing,
   mixed-provenance row, non-ISO decision_date key).
2. Migration `gregory/migrations/0080_historicaltrials_countries_by_source_and_more.py`:
   `Trials.countries_by_source` (JSONField), `Trials.regions_normalized` (JSONField), and
   the `TrialCountry` model.
3. Importer changes: `feedreader_trials_ctgov.py` writes `countries_by_source["ctgov"]`;
   `importWHOXML.py` writes `countries_by_source["ictrp"]` (each touching only its own key,
   via `registry_utils.merge_countries_by_source`). Legacy `countries` unchanged. Covered by
   `gregory/tests/test_trial_countries_by_source_importers.py`.
4. `Trials.save()` wiring via the extended multi-input `NORMALIZED_TRIAL_FIELDS`, plus the
   post-save `TrialCountry` replace-sync (`Trials.sync_trial_countries()`).
5. `backfill_trial_normalized_fields` extended to seed `countries_by_source` from the legacy
   column by format detection (built into `normalize_countries`, so the backfill gets it for
   free), and to call `sync_trial_countries()` for every scanned trial when the `regions`
   field is selected. **Not run against any live database as part of this change** — the
   command supports it; the prod run is a separate step (see runbook below).
6. API: `TrialSerializer` gains `trial_countries` (objects: country, status, decision_date,
   sources — prefetched via `TrialViewSet`/`TrialSearchView` `get_queryset()`),
   `countries_normalized` (flat code list), `regions_normalized`; filters `?country=DE` and
   `?region=europe` in `api/filters.py`. Existing `countries` field kept as-is.
7. Docs: `docs/trials-field-normalization.md` and `docs/trials-multi-source-merge.md`
   extended with the multi-input registry note and the `countries_by_source` join rules.

## Prod runbook (not yet executed)

1. Deploy this change (migration `0080_historicaltrials_countries_by_source_and_more`).
2. `docker exec gregory python manage.py migrate`
3. One-time: `docker exec gregory python manage.py backfill_trial_normalized_fields`
   (backfills `phase_normalized`, `recruitment_status_normalized`, and `regions_normalized`
   in one pass, and rebuilds every trial's `TrialCountry` rows since `--field` defaults to
   every registered field). To scope just this layer:
   `docker exec gregory python manage.py backfill_trial_normalized_fields --field regions`.
4. Optional: review the OTHER list(s) the backfill prints for `phase`/`recruitment_status`,
   and the `Unmapped trial country value: ...` log lines for `regions`, then extend the
   relevant mapping table, deploy, rerun.

## Open questions / deviations from a literal reading of the spec

- The tokenizer splits on a bare `,` (not just `", "`) when no semicolon is present, so it
  also handles compact values like `Japan,Asia(except Japan),Europe` without a following
  space. This is a superset of "split on `", "`" and does not change behaviour for any
  already-space-delimited value.
- `TrialCountry.Meta` uses `UniqueConstraint(fields=["trial", "country"], ...)` rather than
  `unique_together`, matching the rest of `Trials.Meta` in this codebase (which uses
  `UniqueConstraint` throughout, not `unique_together`).
- XLSX export (`export_trials_xlsx.py`) was not extended to include the new columns — the
  rollout steps above (from the original audit) scope the API/serializer/filter wiring but
  not the XLSX exporter; left as a follow-up.
