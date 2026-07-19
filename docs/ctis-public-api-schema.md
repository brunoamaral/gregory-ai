# CTIS public API (`euclinicaltrials.eu/ctis-public-api/`) — observed schema

Empirical documentation of the undocumented JSON API behind the CTIS public search
portal. Everything below was **observed live on 2026-07-18** (responses saved during the
audit session); nothing comes from an official spec — EMA publishes none. The same API
root serves the RSS feed EMA points subscribers at, and community tooling (CRAN
`ctrdata`, `euclinicaltrials.py`) relies on these endpoints, so they are public and
stable-in-practice — but the contract is **unguaranteed**. If it drifts, re-derive it
from the network calls of https://euclinicaltrials.eu/ctis-public/search in browser dev
tools.

Access notes: no authentication, no API key. `GET /search` returns **403** — search is
POST-only (a browser can only explore `retrieve` and the RSS). Requests at 0.3–0.5 s
spacing worked without throttling; `size=100` accepted.

## Endpoints

### 1. `POST /ctis-public-api/search` — paginated trial overviews

```
POST https://euclinicaltrials.eu/ctis-public-api/search
Content-Type: application/json

{
  "searchCriteria": {"medicalCondition": "Multiple Sclerosis"},
  "pagination": {"page": 1, "size": 100},
  "sort": {"property": "decisionDate", "direction": "DESC"}
}
```

`searchCriteria` keys verified live (all composable):

| Key | Type | Verified example → totalRecords |
|:----|:-----|:--------------------------------|
| `medicalCondition` | string | "Multiple Sclerosis" → 126 |
| `sponsor` | string | "Roche" → 223 |
| `number` | string | "2025-523726-40-00" → 1 (exact CT number lookup) |
| `containAll` | string | "ocrelizumab" → 36 (full-text) |
| `status` | int array | `[4]` → 3,848 global; + medicalCondition → 33 |

The portal's advanced-search UI suggests more keys exist (ageGroup, gender, phase,
country, …) — unverified; derive from dev tools if needed.

`pagination`: `page` is 1-based; `size` up to at least 100. Response echoes
`{"totalRecords", "currentPage", "totalPages", "nextPage", "prevPage"}`.

`sort.property`: `decisionDate`, `lastUpdated`, `lastPublicationUpdate` all accepted
(`direction`: ASC/DESC). **Caveat:** observed ordering was not strictly monotonic on the
displayed date field, so an incremental fetcher must not assume perfect ordering — keep
paginating until records are older than the window on *both* `lastUpdated` and
`lastPublicationUpdate`, with an overlap.

Response record schema — union over 100 MS records (coverage = how many of 100 carried
the key):

| Field | Type | Coverage | Notes / sample |
|:------|:-----|:---------|:---------------|
| `ctNumber` | str | 100 | `2026-525260-18-00` — the EUCT number; join key for `retrieve` and our `euct`/`ctis` identifiers |
| `ctStatus` | int | 100 | Public status **code** — see code table below |
| `ctTitle` | str | 100 | Full title |
| `shortTitle` | str | 84 | Often a protocol code ("CN46182"), sometimes an acronym |
| `conditions` | str | 100 | Comma-joined condition names |
| `trialCountries` | list[str] | 100 | `"Name:code"` pairs, EEA member states only, e.g. `["Spain:4","France:2"]` — per-country public status codes (same code table) |
| `decisionDateOverall` | str | 100 | `DD/MM/YYYY` (all API dates are day-first) |
| `decisionDate` | str | 100 | Per-country: `"IT: 24/06/2026, ES: 26/06/2026"` |
| `therapeuticAreas` | list[str] | 100 | MeSH-style labels |
| `sponsor` | str | 100 | Sponsor display name |
| `sponsorType` | str | 100 | Raw label ("Pharmaceutical company", …). **Comma-duplicated when multi-sponsor** ("Hospital/…, Hospital/…") — dedupe at write. Full coverage: 100/100 (vs RSS-era 49/176 in our DB) |
| `trialPhase` | str | 100 | Registry phrasing ("Therapeutic confirmatory  (Phase III)") — feed to `normalize_phase` as-is |
| `product` | str | 100 | Product/brand names |
| `endPoint` / `primaryEndPoint` | str | 100 | Secondary / primary endpoints text |
| `ageGroup` | str | 100 | "18-64 years" |
| `ageRangeSecondary` | list | 100 | Usually `[""]` — ignore |
| `gender` | str | 100 | "Female, Male" |
| `trialRegion` | int | 100 | 1 or 3 observed; retrieve shows 3="Both" (EEA+non-EEA); 1=EEA-only (inferred, verify before relying) |
| `totalNumberEnrolled` | str | 100 | Numeric string |
| `resultsFirstReceived` | str | 100 | "Yes"/"No" |
| `lastUpdated` | str | 100 | `DD/MM/YYYY` |
| `lastPublicationUpdate` | str | 100 | `DD/MM/YYYY` |
| `startDateEU` | str | 84 | `DD/MM/YYYY` |
| `endDateEU` | str | 17 | `DD/MM/YYYY` |
| `endDate` | str | 7 | `DD/MM/YYYY` |

### 2. `GET /ctis-public-api/retrieve/{ctNumber}` — full single-trial record

Browser-explorable, e.g.:
`https://euclinicaltrials.eu/ctis-public-api/retrieve/2025-523726-40-00`
(~85 KB of JSON for that trial.)

Top-level: `ctNumber`, `ctStatus` (**string label — application status, NOT the public
status**; see code table), `startDateEU`, `decisionDate`, `publishDate`,
`ctPublicStatusCode` (int — matches `search.ctStatus`), `authorizedApplication`,
`events`, `results`, `documents`, `trialRegion` (label) + `trialRegionCode`,
`correctiveMeasures`.

Notable subtrees (field names abridged; see saved sample in the session scratchpad or
re-fetch):

- `authorizedApplication.authorizedPartI`
  - `sponsors[]` — `primary` flag, `publicContacts[]`/`scientificContacts[]` each with
    `functionalEmailAddress`, `telephone`, and an **`organisation` object: `name`,
    `type` (label), `typeCode`, `commercial` (bool), and `businessKey` — an EMA
    **OMS organisation ID** (`ORG-100001445` for F. Hoffmann-La Roche AG), i.e. an
    authoritative canonical sponsor identifier**. `thirdParties[]` lists CROs/vendors
    with their own organisation objects and addresses.
  - `products[]` — `productName`, `jsonActiveSubstanceNames` (active substance),
    `evCode` (EudraVigilance product code `PRD…`), `sponsorProductCodeEdit`,
    `pharmaceuticalFormDisplay`, routes, devices, dosing fields.
  - `trialDetails.trialInformation` — structured versions of what we hold as free text:
    `eligibilityCriteria.principalInclusionCriteria`/`principalExclusionCriteria`,
    `endPoint.primaryEndPoints`/`secondaryEndPoints`, `trialObjective`,
    `populationOfTrialSubjects` (age ranges, groups, vulnerable-population flags),
    `trialDuration` (`estimatedRecruitmentStartDate`, `estimatedEndDate`,
    `estimatedGlobalEndDate`), `sourceOfMonetarySupport[]` (funder org names),
    `individualParticipantData` (IPD sharing plan).
  - `rowCountriesInfo[]` — **all** participating countries incl. non-EEA (Brazil in the
    sample) with `isoAlpha2Code`/`isoAlpha3Code`/`isoNumber` — cleaner than
    `country_status`, which is EEA-only.
  - `medicalConditions[]` (+ `isConditionRareDisease`), `therapeuticAreas[]`,
    `trialCategoryCode`, `isLowIntervention`.
- `authorizedApplication.authorizedPartsII[]` — one per member state:
  - `mscInfo` — per-country `trialStatus`, `firstDecisionDate`,
    `trialRecruitmentPeriod[]` (**actual recruitment start dates per country**),
    `hasRecruitmentStarted`, `clinicalTrialStatusHistory`.
  - `trialSites[]` — **site-level data**: organisation `name` + `type`
    ("Hospital/Clinic/…"), full address (`city`, `postcode`, `countryName`), phone,
    email, and investigator `personInfo` (first/last name). 6 sites in the sample
    Part II.
  - `recruitmentSubjectCount` per country.
- `authorizedApplication.memberStatesConcerned[]` — `mscName`,
  `mscPublicStatusCode` (same code table), first/last decision dates.
- `events` — `temporaryHaltList`, `trialEvents` (per MSC), `unexpectedEvents`,
  `seriousBreaches`, `urgentSafetyMeasures`.
- `results` — empty dict in the sample (trial too new); expect summary-results content
  for reported trials.
- `documents[]` — 39 entries in the sample: `title`, `uuid`, `documentTypeLabel`
  ("Recruitment arrangements (for publication)", …), `fileType`, versions. (Download
  URL pattern not probed.)
- `authorizedApplication.eudraCt.isTransitioned` — whether the trial migrated from
  EudraCT.

### 3. `GET /ctis-public-api/rss/updates.rss?search_criteria=<url-encoded JSON>`

The existing feedreader source. Returns only the **15 most recently updated** trials for
the criteria; description HTML carries labeled lines (`Sponsor`, `Sponsor type`,
`Overall trial status`, `Status in each country`, …). Kept as the fallback channel — see
`TRIALS-SPONSOR-CANONICALIZATION-PLAN.md` PR A.

## Public status codes (empirical)

Derived by joining the RSS labels to `search` records for the same trials (counts =
observations); code 11 from a `retrieve` of a terminal-state trial. The same code
vocabulary is used at trial level (`search.ctStatus`, `retrieve.ctPublicStatusCode`) and
country level (`trialCountries`, `mscPublicStatusCode`):

| Code | Label (as the RSS/portal renders it) | Evidence |
|:-----|:-------------------------------------|:---------|
| 2 | Authorised, recruitment pending | 10 country obs |
| 3 | Authorised, recruiting | 1 country obs |
| 4 | Ongoing, recruiting | 5 trial + 30 country obs |
| 5 | Ongoing, recruitment ended | 8 trial + 51 country obs |
| 8 | Ended | 2 trial + 33 country obs |
| 11 | Not authorised | retrieve label, 1 obs |

Codes 1, 6, 7, 9, 10, 12+ exist in principle (portal filters list more statuses:
"Ongoing, other", "Temporarily halted", "Suspended", "Ended prematurely", "Expired",
"Revoked"…) but were **not observed** — an importer must log-and-skip unknown codes,
never write the bare number. **Trap:** `retrieve.ctStatus` (string) is the
*application* status ("Authorised" for a trial whose public status is
"Ongoing, recruiting") — never use it as the recruitment status; use the code + this
table.

## Data we could use beyond sponsor/sponsor_type

Ranked by how directly it serves known roadmap items:

1. **Sponsor OMS `businessKey`** (`ORG-…`, retrieve) — authoritative entity id for the
   sponsor canonicalization work; an alias anchored to an OMS id never needs curation.
   Plus `commercial` flag and org `typeCode` (sponsor_type signal, structured).
2. **Per-country recruitment periods + status history** (retrieve `mscInfo`) — real
   per-country recruitment start dates; richer than `country_status` and fresher than
   the RSS window. Serves the world-map "recruiting near me" view.
3. **Site-level geography** (retrieve `trialSites`: institution, city, country,
   investigator) — lifts the "country is the geography ceiling" limit from the
   visualizations audit for EU trials. (CTGov's API has site `geoPoint` lat/lon for the
   same ambition on its side.)
4. **All-countries ISO list** (`rowCountriesInfo` incl. non-EEA) — a cleaner input to
   `normalize_countries` than parsing `country_status`.
5. **Products / active substances / EV codes** — structured intervention data; feeds the
   P2 category-modality work.
6. **Structured eligibility criteria + endpoints + estimated dates** — upgrade for the
   free-text `inclusion_criteria`/`exclusion_criteria`/outcome columns and for
   time-series (estimated recruitment start / end dates).
7. **Events (temporary halts, urgent safety measures)** and **documents** metadata —
   status nuance and linkable artifacts (documents also interest the personal
   clinical-trial-note workflow).
8. `resultsFirstReceived` / `results` — corroborates `has_results`.

## Saved samples

The original session-scratchpad captures (`ctis_search_p1.json` — 100 MS search
records; `ctis_retrieve.json` — full retrieve payload for 2025-523726-40-00; `ctis.rss`
— RSS snapshot used for the code↔label join) are not committed, but the trial they were
drawn from (2025-523726-40-00) is preserved as a fixture in
`django/gregory/tests/test_ctis_public_api.py` (`RssParityTests`), which asserts the RSS
and `/search` API channels map to identical `extra_fields` for that trial — this is the
byte-compatibility contract `feedreader_trials_ctis` depends on. Re-derive fresh samples
from the network calls of https://euclinicaltrials.eu/ctis-public/search if this
contract needs re-verification.
