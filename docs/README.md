# GregoryAI documentation

Index of the docs in this folder. The numbered `00–06` files are the guided
walkthrough (read in order); everything else is reference or design material.

> **Keeping docs current:** when you change a filter, serializer, view, model, or
> route, update [03-api-and-rss-feeds.md](03-api-and-rss-feeds.md) (and the relevant
> view docstring / [02.1-database-tables-and-fields.md](02.1-database-tables-and-fields.md))
> **in the same PR** — that is what keeps this folder from drifting out of sync with the code.

## Guided walkthrough

| Doc | What it covers |
|:----|:---------------|
| [00-intro.md](00-intro.md) | What GregoryAI is and how the pieces fit together. |
| [01-install.md](01-install.md) | Local development setup. |
| [02-sources-and-articles.md](02-sources-and-articles.md) | Sources, feeds, and how articles are ingested. |
| [02.1-database-tables-and-fields.md](02.1-database-tables-and-fields.md) | Schema reference — the main tables and their fields. |
| [03-api-and-rss-feeds.md](03-api-and-rss-feeds.md) | **Canonical API reference** — endpoints, query parameters, auth, search, stats, RSS. |
| [04-machine-learning.md](04-machine-learning.md) | Overview of the ML relevance pipeline. |
| [05-training-models.md](05-training-models.md) | Training and evaluating the ML models. |
| [06-organisations-teams-and-sites.md](06-organisations-teams-and-sites.md) | Organisations, teams, sites, and visibility scoping. |

## API & data reference

| Doc | What it covers |
|:----|:---------------|
| [authors-api.md](authors-api.md) | Authors API — filtering, sorting, and category/timeframe options. |
| [csv-export.md](csv-export.md) | CSV export options across the list and search endpoints. |
| [glossary.md](glossary.md) | Definitions of core terms. |
| [ctis-public-api-schema.md](ctis-public-api-schema.md) | Observed schema of the EU CTIS public API. |
| [clinicaltrialsgov-api/](clinicaltrialsgov-api/) | Reference snapshots of the ClinicalTrials.gov API. |

> Search endpoints (`/articles/search/`, `/trials/search/`, `/authors/search/`) are
> documented in [03-api-and-rss-feeds.md § Search endpoints](03-api-and-rss-feeds.md#search-endpoints).

## Design notes

| Doc | What it covers |
|:----|:---------------|
| [ml-consensus.md](ml-consensus.md) | ML consensus modes and probability thresholds. |
| [trials-field-normalization.md](trials-field-normalization.md) | How raw registry fields are normalized (status, phase, sponsor, countries, …). |
| [trials-multi-source-merge.md](trials-multi-source-merge.md) | Merging trial data across registries. |
| [article-doi-dedup.md](article-doi-dedup.md) | Article de-duplication by DOI. |
| [streaming-csv-response.md](streaming-csv-response.md) | Implementation of streamed CSV responses. |
| [spec-ml-training.md](spec-ml-training.md) | Specification for the ML training workflow. |

## Operations & commands

| Doc | What it covers |
|:----|:---------------|
| [cookbook.md](cookbook.md) | Recipes for common tasks (pipeline, exports, feeds). |
| [subscriptions.md](subscriptions.md) | Subscription/newsletter system. |
| [merge-authors-command.md](merge-authors-command.md) | The author-merge management command. |
| [lint-triage.md](lint-triage.md) | Ruff lint-gate worklist. |

## History

| Location | What it covers |
|:---------|:---------------|
| [changelog/](changelog/) | Per-feature implementation notes, newest work first. |
| [releases/](releases/) | Release notes, deployment runbooks, and migration-safety checks. |
