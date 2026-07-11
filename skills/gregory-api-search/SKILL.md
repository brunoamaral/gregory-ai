---
name: gregory-api-search
description: >
  Search the GregoryAI research database (https://api.brain-regeneration.com) read-only:
  find scientific articles and clinical trials on a medical or neuroscience topic, run
  boolean title/abstract searches, filter by DOI, registry ID (NCT/EudraCT/EUCT/CTIS),
  journal, date, or AI-relevance, look up authors, browse research subjects and categories,
  and get RSS feeds. Use whenever someone wants to find papers or trials, explore this
  research corpus, or pull results as JSON/CSV. Read-only — no data is ever created or modified.
---

# GregoryAI API Search

GregoryAI aggregates clinical research — ~49,000 articles and ~29,000 clinical trials —
tagged by team, research subject, and category, with machine-learning relevance scores.
This skill queries it **read-only** over plain HTTP. No API key or login is required for reads.

**Base URL:** `https://api.brain-regeneration.com`

## Golden rules

- **Read-only.** Only ever issue `GET` requests. Never suggest or attempt to create, edit, or delete anything.
- **Ask for JSON.** Append `?format=json` (or send header `Accept: application/json`) or you may get an HTML page.
- **Results are paginated.** Default 10 per page, max `page_size=100`. Response wraps results in
  `count`, `next`, `previous`, `current_page`, `total_pages`, `page_size`, `results`.
  Follow the `next` URL to page, or add `all_results=true` to return everything (use sparingly — some
  result sets are huge).
- **CSV export:** add `format=csv&all_results=true` instead of `format=json`.
- Some collections are large (48k+ articles, 248k+ authors) — **always** narrow with filters before fetching.

## How to call it

Use `curl` (preferred for scripting/paging) or `WebFetch`. Every example below is a complete URL.

```bash
curl -s "https://api.brain-regeneration.com/articles/?search=microglia&page_size=5&format=json"
```

## The one search rule you must know

The `search=` parameter (on `/articles/` and `/trials/`) runs a **boolean search over title + abstract**
(articles) or **title + summary** (trials):

- Space-separated terms are **AND**-ed: `search=stem cells regeneration`
- `OR` (uppercase) for alternatives: `search=alzheimer OR parkinson`
- `-term` or `NOT term` to exclude: `search=covid -vaccine`
- `"quoted phrases"` match contiguously: `search="multiple sclerosis"`
- `(parentheses)` group: `search=(microglia OR astrocyte) AND inflammation`

URL-encode it: spaces → `%20` (or `+`), quotes → `%22`. See [references/search-syntax.md](references/search-syntax.md).

## Endpoint map

| You want… | Endpoint | Reference |
|---|---|---|
| Search / filter **articles** | `GET /articles/` | [articles.md](references/articles.md) |
| One article by id | `GET /articles/{article_id}/` | [articles.md](references/articles.md) |
| Search / filter **clinical trials** | `GET /trials/` | [trials.md](references/trials.md) |
| One trial by id | `GET /trials/{trial_id}/` | [trials.md](references/trials.md) |
| Look up **authors** | `GET /authors/` | [authors.md](references/authors.md) |
| List **research subjects** (topics) | `GET /subjects/` | [subjects-and-categories.md](references/subjects-and-categories.md) |
| List **categories** | `GET /categories/` | [subjects-and-categories.md](references/subjects-and-categories.md) |
| **RSS** feeds | `GET /feed/...` | [rss-feeds.md](references/rss-feeds.md) |

## Discover the topics first (subjects & teams)

Most filtering keys off **subject** (a research topic) and **team**. Look them up first — every
subject row carries its `team_id`:

```bash
curl -s "https://api.brain-regeneration.com/subjects/?format=json"
```

Subjects on this instance (`subject_id` → topic). Pass `subject_id` to `/articles/` and
`/trials/` to scope a search to that research area:

| subject_id | topic | team_id | description |
|---|---|---|---|
| 1 | Multiple Sclerosis | 1 | Clinical Trials and Articles for MS |
| 10 | Cell Reprogramming | 4 | Turning the body's own non-neuronal cells into functioning neurons; epigenetic editing for brain self-repair |
| 11 | Neuroimmune Interactions | 5 | — |
| 12 | Neuroinflammation | 6 | Peripheral Inflammation — CNS |
| 13 | Alzheimer's Disease | 1 | Clinical Trials for Alzheimer's Disease |
| 14 | Parkinson's Disease | 1 | All Clinical Trials for Parkinson's Disease across WHO and CTGov |

(IDs can change and new subjects may be added — re-fetch `/subjects/` to confirm the current list.)

## Quick-start recipes

```bash
BASE="https://api.brain-regeneration.com"

# 1. Recent articles mentioning microglia
curl -s "$BASE/articles/?search=microglia&ordering=-published_date&page_size=10&format=json"

# 2. AI-relevant Multiple Sclerosis articles (subject 1), most confident first
curl -s "$BASE/articles/?subject_id=1&relevant=true&ordering=-ml_score&format=json"

# 3. Article by DOI
curl -s "$BASE/articles/?doi=10.1007/s00296-026-06233-x&format=json"

# 4. Recruiting Parkinson's trials (subject 14)
curl -s "$BASE/trials/?subject_id=14&status=Recruiting&format=json"

# 5. A trial by its NCT number
curl -s "$BASE/trials/?nct=NCT04305002&format=json"

# 6. Find an author by name, see their article count
curl -s "$BASE/authors/?full_name=smith&sort_by=article_count&order=desc&format=json"

# 7. Alzheimer's articles published in 2024, as CSV
curl -s "$BASE/articles/?subject_id=13&published_date_after=2024-01-01&published_date_before=2024-12-31&format=csv&all_results=true"
```

## Reference files

Read the relevant reference file before building anything non-trivial — each lists every
supported filter, the exact response fields, and worked examples:

- [references/articles.md](references/articles.md) — all article filters, ML relevance, dates, ordering
- [references/trials.md](references/trials.md) — registry-ID lookups, conditions, sponsors, results
- [references/authors.md](references/authors.md) — author search and their publication lists
- [references/subjects-and-categories.md](references/subjects-and-categories.md) — topic & category discovery
- [references/search-syntax.md](references/search-syntax.md) — full boolean `search=` grammar
- [references/rss-feeds.md](references/rss-feeds.md) — author and trial RSS feeds
