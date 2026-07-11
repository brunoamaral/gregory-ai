# Authors — `GET /authors/`

Look up researchers (~249k) and their publication activity. No team/subject required.
Single author: `GET /authors/{author_id}/`.

## Filters

| Param | Meaning |
|---|---|
| `search` | Match over full name and ORCID. |
| `full_name` | Case-insensitive partial match on full name. |
| `given_name` | Partial match on first name. |
| `family_name` | Partial match on surname. |
| `author_id` | Exact author id. |
| `orcid` | Partial match on ORCID iD. |
| `country` | Exact country. |

## Ordering & pagination

- `sort_by` — `author_id` (default), `full_name`, `country`, `article_count`.
- `order` — `asc` or `desc` (default: `desc` for `article_count`, `asc` otherwise).
  Note: unlike `/articles/` and `/trials/`, this endpoint does **not** use DRF's `ordering=` param.
- `page`, `page_size` (≤100), `all_results=true`, `format=json|csv`.

## Response fields

`author_id`, `given_name`, `family_name`, `full_name`, `ORCID`, `country`, `biography`,
`articles_count`, `relevant_articles_count`, `articles_list` (their articles).

## Get an author's articles

Two ways:
- Read `articles_list` on the author record, or
- Query articles directly: `GET /articles/?author_id={author_id}` (supports all article
  filters — dates, relevance, ordering; see [articles.md](articles.md)).

## Examples

```bash
BASE="https://api.brain-regeneration.com"

# Find authors by surname, most-published first
curl -s "$BASE/authors/?family_name=smith&sort_by=article_count&order=desc&format=json"

# Look up by ORCID
curl -s "$BASE/authors/?orcid=0000-0002-1825-0097&format=json"

# One author and their recent papers
curl -s "$BASE/authors/547318/?format=json"
curl -s "$BASE/articles/?author_id=547318&ordering=-published_date&format=json"
```
