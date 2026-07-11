# Clinical trials — `GET /trials/`

Search and filter clinical trials across the whole corpus (~29k). **No team_id or subject_id
required** — query the whole set. Add `subject_id` to scope to a research area.

Single trial: `GET /trials/{trial_id}/`.

## Search & text filters

| Param | Meaning |
|---|---|
| `search` | Boolean search over **title + summary**. AND/OR/NOT/`-`/`"phrase"`/`()`. See [search-syntax.md](search-syntax.md). |
| `title` / `summary` | Substring match in that field. |
| `condition` | Condition/disease, substring match. |
| `intervention` | Intervention/drug, substring match. |
| `therapeutic_areas` | Therapeutic area, substring. |
| `primary_sponsor` | Sponsor name, substring. |

## Registry-ID lookups

Each accepts one value or a comma-separated list; case-insensitive; matches *any*.

| Param | Registry |
|---|---|
| `nct` | ClinicalTrials.gov NCT number(s). |
| `eudract` | EudraCT number(s). |
| `euct` | EU CT / EUCTR number(s). |
| `ctis` | CTIS number(s). |
| `identifiers` | **Any** registry — mixed list matched across NCT/EudraCT/EUCT/CTIS at once. |
| `acronym` | Trial acronym(s). |
| `trial_id` | Internal GregoryAI trial id. |

## Status / design filters

| Param | Meaning |
|---|---|
| `status` (a.k.a. `recruitment_status`) | e.g. `Recruiting`, `Completed`, `Active, not recruiting`. |
| `phase` | e.g. `Phase 2`, substring. |
| `study_type` | e.g. `Interventional`, substring. |
| `source_register` | Originating registry, substring. |
| `countries` | Country name, substring. |
| `inclusion_gender` | Substring, e.g. `Female`. |
| `inclusion_agemin` / `inclusion_agemax` | Exact age bound. |
| `has_results` | `true`/`false` — results posted/available. |

## Scope filters

`subject_id`, `subjects` (AND), `subjects_any` (OR), `team_id`, `category_slug`, `category_id`,
`source_id` — same semantics as articles.

## Date / ordering / pagination

- `date_registration_after` / `date_registration_before` — `YYYY-MM-DD`.
- `ordering` — `discovery_date`, `published_date`, `title`, `trial_id`, `last_updated` (prefix `-`).
- `page`, `page_size` (≤100), `all_results=true`, `format=json|csv`.

## Response fields (per trial)

Includes: `trial_id`, `title`, `scientific_title`, `summary`, `ctg_detailed_description`,
`identifiers` (JSON of registry IDs), `acronym`, `recruitment_status`, `phase`, `study_type`,
`study_design`, `condition`, `intervention`, `primary_outcome`, `secondary_outcome`,
`inclusion_criteria`, `exclusion_criteria`, `inclusion_agemin`/`agemax`/`gender`, `target_size`,
`primary_sponsor`, `secondary_sponsor`, `sponsor_type`, `source_register`, `countries`,
`date_registration`, `date_enrollement`, `therapeutic_areas`, `results_posted`, `results_url_link`,
`results_yes_no`, `link`, `links`, `sources`, `team_categories`, `articles` (linked articles),
and contact/ethics fields.

## Examples

```bash
BASE="https://api.brain-regeneration.com"

# A specific trial by NCT id
curl -s "$BASE/trials/?nct=NCT04305002&format=json"

# Mixed registry ids in one call
curl -s "$BASE/trials/?identifiers=NCT04305002,2019-001416-23&format=json"

# Recruiting Parkinson's trials (subject 14) with results, newest registration first
curl -s "$BASE/trials/?subject_id=14&status=Recruiting&has_results=true&ordering=-date_registration&format=json"

# Phase 3 trials for a condition
curl -s "$BASE/trials/?condition=multiple%20sclerosis&phase=Phase%203&format=json"

# Boolean search across trial text
curl -s "$BASE/trials/?search=%22remyelination%22%20AND%20(safety%20OR%20tolerability)&format=json"
```
