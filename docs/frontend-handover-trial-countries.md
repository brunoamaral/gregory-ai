# Frontend handover: normalized trial countries & regions

> Audience: frontend engineers consuming the GregoryAI trials API.
> Source: [PR #769](https://github.com/brunoamaral/gregory-ai/pull/769) — branch `claude/trial-country-normalization-impl`.
> Status: **not yet merged/deployed.** Nothing below is live until this PR merges, migrates, and (for existing trials) the one-time backfill runs in prod — see [Rollout status](#rollout-status).

## Why

The `countries` field on a trial has always been a raw, per-registry string: ClinicalTrials.gov writes comma-joined display names (`United States, France`), WHO ICTRP writes semicolon-joined UN-style names (`Iran (Islamic Republic of);Korea, Republic of`), and EU CTIS doesn't populate it at all. Across the dataset that's ~169 distinct spellings for what should be ~120 countries — typos, UK subdivisions (`England`, `Scotland`), continent names, casing variants, four spellings of South Korea. It's also last-write-wins: if a trial is synced from both CTGov and WHO, whichever ran more recently overwrites the other's country list.

This PR adds a normalized layer on top, without touching `countries`. **`countries` is unchanged and still returned as-is** — keep using it only if you specifically want the raw per-registry string. For anything else (filtering, display, grouping by geography), switch to the new fields below.

## New response fields

Available on every trial object returned by `GET /trials/`, `GET /trials/{id}/`, and `GET /trials/search/` (all three use the same serializer).

### `countries_normalized`

Flat, sorted list of ISO 3166-1 alpha-2 codes derived from **all** sources (site locations, WHO recruitment countries, and EU CTIS regulatory decisions combined — union, not last-writer).

```json
"countries_normalized": ["DE", "FR", "US"]
```

`null` when the trial has no country data in any raw column (still true for ~48% of trials in dev — mostly legacy ClinicalTrials.gov trials imported before the API-based importer existed; see [Known gaps](#known-gaps)).

### `trial_countries`

Per-country breakdown — the same country set as `countries_normalized`, but with EU CTIS status/date/provenance attached where known.

```json
"trial_countries": [
  {
    "country": "DE",
    "status": "recruiting",
    "decision_date": "2024-07-19",
    "sources": ["ctgov", "ctis"]
  },
  {
    "country": "FR",
    "status": null,
    "decision_date": null,
    "sources": ["ctgov"]
  }
]
```

| Field | Type | Notes |
|:------|:-----|:------|
| `country` | string | ISO 3166-1 alpha-2 code |
| `status` | string \| null | EU CTIS per-country authorisation status, one of the `recruitment_status_normalized` vocabulary: `not_yet_recruiting`, `recruiting`, `enrolling_by_invitation`, `active_not_recruiting`, `not_recruiting`, `suspended`, `completed`, `terminated`, `withdrawn`, `unknown`, `other`. **Only populated for countries sourced from EU CTIS** — a country contributed solely by ClinicalTrials.gov or WHO has `status: null` (site/recruitment-country data doesn't carry a per-country status). |
| `decision_date` | string (`YYYY-MM-DD`) \| null | EU CTIS per-country regulatory decision date. Same CTIS-only caveat as `status`. |
| `sources` | string[] | Which registries contributed this country: `ctgov`, `ictrp` (WHO), `ctis`. A country can have more than one, e.g. `["ctgov", "ctis"]` if both a CTGov site and a CTIS authorisation exist for it. |

Empty array `[]` (not `null`) when `countries_normalized` is also empty — use whichever check is more convenient (`.length === 0` or `!countries_normalized`).

### `regions_normalized`

Sorted list of continental region slugs derived from the trial's normalized countries, plus any literal region tokens found in the raw data (e.g. WHO rows that say `"Europe"` instead of naming a country).

```json
"regions_normalized": ["europe", "north_america"]
```

Possible values: `africa`, `asia`, `europe`, `north_america`, `south_america`, `oceania`. Central America and the Caribbean fold into `north_america`. `null` when there's nothing to derive a region from.

## New filters

Same two query params work on both `GET /trials/` and `GET /trials/search/`.

| Param | Type | Behavior |
|:------|:-----|:---------|
| `?country=DE` | ISO alpha-2, case-insensitive | Returns trials whose normalized country set includes this code. Exact match — `?country=de` and `?country=DE` are equivalent, but there's no partial/`icontains` matching. |
| `?region=europe` | one of the 6 region slugs | Exact match against `regions_normalized`. Invalid values (not one of the 6) return a 400. |

The **existing** `?countries=France` filter (free-text `icontains` on the raw `countries` column) is unchanged and still works — it's just as unreliable as before (misses spelling variants, doesn't see CTIS-only trials at all). Prefer `?country=` / `?region=` for anything new.

```bash
# All recruiting trials with a German site or CTIS authorisation
GET /trials/?country=DE&recruitment_status_normalized=recruiting

# All European trials for a subject
GET /trials/?subject_id=2&region=europe
```

## Migration guidance

- **Do not remove `countries` handling yet** — it's staying for backward compatibility and is the only field for trials whose country data hasn't normalized cleanly (rare, but the raw column is the fallback of record).
- For **display** (e.g. a flag/country chip list on a trial card): use `trial_countries`, rendering `country` through your own ISO-to-name/flag lookup (the API returns the code, not a display name — same pattern as `Authors.country` elsewhere in this API).
- For **filtering/faceting UI** (e.g. a country or region dropdown): use `?country=` / `?region=` against `countries_normalized` / `regions_normalized`. The `region` values map directly to a fixed 6-item dropdown; the `country` values are a subset of ISO alpha-2 (whatever's actually present in the data — there's no separate "list all countries" endpoint yet, so populate a country filter from observed data or a static ISO list, not from the API).
- For a **per-country regulatory status view** (CTIS-specific — e.g. "authorised in these 5 EU countries, pending in these 2"): use `trial_countries[].status` / `.decision_date`, filtering client-side to entries with non-null `status`.

## Known gaps

- **~48% of trials have no country data at all** (dev DB snapshot) — mostly legacy ClinicalTrials.gov trials imported before the current API-based importer existed, so there are no site locations to normalize. `countries_normalized` / `trial_countries` / `regions_normalized` are all `null`/`[]` for these; this isn't something the frontend can work around, it's a data-availability gap being tracked separately (a future backfill would re-fetch these trials by NCT id).
- **EU CTIS only reports EEA countries.** A trial flagged `trial_region: "In both EEA and non-EEA"` (raw CTIS field, unchanged by this PR) has sites outside the EEA that CTIS simply doesn't publish — they won't appear in `trial_countries` unless WHO or CTGov also cover the trial. There's no way to detect this from the normalized fields alone; if you need to flag "may be incomplete," check `trial_region` directly.

## Rollout status

- **Not merged.** Live on branch `claude/trial-country-normalization-impl` in [PR #769](https://github.com/brunoamaral/gregory-ai/pull/769).
- Once merged and migrated, **new/updated trials get normalized automatically** on every save.
- **Existing trials need a one-time backfill** (`backfill_trial_normalized_fields`) run in prod after deploy — until that runs, trials that existed before the deploy will show `null`/`[]` for the new fields even though they have country data in `countries` or the CTIS columns. There's no fixed date for this yet; check with backend before assuming the data is populated.

## Reference

Full design rationale and the token-mapping details: `TRIAL-COUNTRY-NORMALIZATION-PLAN.md` (repo root) and `docs/trials-multi-source-merge.md`.
