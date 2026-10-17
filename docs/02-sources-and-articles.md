# Sources and articles

> Audience: operators and developers configuring GregoryAI sources and working with the article model.

Some core concepts before we start. GregoryAI uses the term **Article** to refer to both science papers published in journals and news articles from other sources.

A **Source** is any website from which GregoryAI can extract articles.

A **Subject** is a group of Sources and their respective articles.

A **Category** is a group of articles whose title matches at least one keyword in a term list for that category. Categories can include articles across subjects.

---

## Sources

Add and edit sources in the Django admin at `/admin/gregory/sources/`. The form asks four
things in order: what kind of source it is, which team and subject its content goes to,
the settings that kind needs, and a name and notes. Only the chosen kind's settings are
shown, and the form refuses to save a source that couldn't work: a feed without a URL,
a search with no query, or a subject from another team.

The kind is one *Source type* choice. Each stores a `source_for`/`method` pair (see
`gregory/source_types.py`):

| Source type | `source_for` / `method` | How content arrives |
|:------------|:------------------------|:--------------------|
| RSS feed of science papers | `science paper` / `rss` | `feedreader_articles` reads the feed on every pipeline run |
| RSS feed of clinical trials | `trials` / `rss` | `feedreader_trials` reads the feed |
| ClinicalTrials.gov search | `trials` / `ctgov_api` | `feedreader_trials_ctgov` runs the search |
| EU CTIS search | `trials` / `ctis_api` | `feedreader_trials_ctis` runs the search |
| PubMed search | `science paper` / `pubmed_api` | `feedreader_articles_pubmed` runs the search on every pipeline run; see [pubmed-sources.md](pubmed-sources.md) |
| EPO patent search | `patents` / `epo_ops` | `feedreader_patents_ops` runs the search on every pipeline run; see [patents.md](patents.md) |
| Science papers, clinical trials or news articles sent by an external scraper | `…` / `scrape` | Never fetched. Content arrives through `POST /articles/post/` with this source's ID |
| Science papers, clinical trials or news articles added by hand or through the API | `…` / `manual` | Never fetched. Added in the admin or through `POST /articles/post/` |

Any other pair is never fetched by anything. Existing sources with such a pair keep it
when edited, and the source list shows them in red as "never fetched", until someone picks
a real type.

Every source needs a name, a team and a subject: sites only show content that has a
subject. The *Notes* field (`description`) is for people and is never read by an importer.

The *Keyword filter* is meant for broad RSS paper feeds that cover many topics, such as
bioRxiv, medRxiv or PNAS: only entries whose title or summary mentions one of the keywords
are kept. Separate keywords with commas and quote phrases: `"multiple sclerosis", alzheimer`.

### ClinicalTrials.gov sources (`method="ctgov_api"`)

Three fields build the search: *Condition or disease* (`ctgov_search_condition`, the
API's `query.cond`), *Intervention or treatment* (`ctgov_search_intervention`,
`query.intr`) and *Other search terms* (`ctgov_search_term`, `query.term`). ClinicalTrials.gov
returns trials that match every field you fill in. At least one is required: with none,
the search would return the whole registry, so the importer skips such a source. Run via
`python manage.py feedreader_trials_ctgov` (also part of the `pipeline` command).

### CTIS public API sources (`method="ctis_api"`)

Fetches the full CTIS (EU Clinical Trials Information System) result set via the
undocumented public search API — see [docs/ctis-public-api-schema.md](ctis-public-api-schema.md)
for the full request/response contract. Unlike the CTIS RSS feed (`method="rss"`,
still active and never retired — it's the EMA-advertised channel and the fallback if
the undocumented API access is ever withdrawn), the API returns every matching trial,
not just the 15 most recently updated.

Configuration: `source_for="trials"`, `link` left empty (search criteria drive the
fetch, not a URL), and `ctis_search_criteria` set to the verbatim `searchCriteria`
dict POSTed to the API, e.g. `{"medicalCondition": "Multiple Sclerosis"}`. Supported
keys: `medicalCondition`, `sponsor`, `number`, `containAll`, `status`. Run via
`python manage.py feedreader_trials_ctis` (also wired into the `pipeline` command).

---

## Articles

| Field | Notes |
|:------|:------|
| `article_id` | Primary key |
| `title` | Article title |
| `summary` | Abstract for science papers; full body for news articles |
| `link` | Source URL |
| `published_date` | From the source feed (or CrossRef) |
| `relevant` | Manual relevance flag set in the admin |
| `ml_prediction_gnb` | Gaussian Naive Bayes prediction |
| `ml_prediction_lr` | Logistic Regression prediction |
| `ml_prediction_lsvc` | Linear SVC prediction |
| `discovery_date` | When GregoryAI first saw the article |
| `noun_phrases` | Noun chunks extracted by spaCy |
| `sent_to_admin` | Whether included in the admin digest |
| `sent_to_subscribers` | Whether included in the weekly digest |
| `source` | Foreign key → Sources |
| `doi` | Digital Object Identifier, used for de-duplication and CrossRef enrichment |
| `kind` | One of: `science paper`, `news`, `trial` |

### Refreshing metadata from CrossRef in the admin

Articles with a `doi` show a **Refresh from CrossRef** button on their admin
change page (articles without a DOI show an inert message instead). It fetches
current CrossRef metadata — plus Unpaywall's `access`/`pdf_link` — and renders a
field-by-field diff against what the article currently holds, alongside any
authors CrossRef lists that aren't attached yet (and any attached authors
CrossRef no longer lists, as opt-in, never pre-ticked, removals). Nothing is
written until you tick the rows you want and submit; only those rows are
applied. A CrossRef value of `None` never blanks a populated field, and a
year-only or year+month `issued` date is flagged with a precision warning
rather than silently applied as a padded 1 January date. Scalar changes are
recorded in the article's History tab; author additions/removals are recorded
as a change-reason note (family names only, to fit the 100-character column)
plus a full detail entry in the admin's own log.

This uses the same `SciencePaper.refresh()` CrossRef/Unpaywall client as the
`update_articles_info` pipeline command — see
[gregory/services/crossref_refresh.py](../django/gregory/services/crossref_refresh.py).

---

## Endpoints by subject and category

Use these formats to filter the article list. The `<subject>` and `<category>` slug is the lowercase name with spaces replaced by dashes.

### RSS feeds

```text
https://example.com/feed/articles/subject/<subject>/
https://example.com/feed/articles/category/<category>/
```

### API endpoints

```text
https://example.com/articles/?subject_id=<id>
https://example.com/articles/?category_slug=<category>
```

For the full list of filtering parameters, see [03-api-and-rss-feeds.md](03-api-and-rss-feeds.md).
