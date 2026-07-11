# Subjects & categories

These are the discovery endpoints. **Subjects** are the research topics you scope searches to
(`subject_id`); **categories** are finer team-defined groupings. Both are read-only.

## Subjects — `GET /subjects/`

A *subject* is a research area (a condition or theme). Its `id` is the `subject_id` you pass to
`/articles/` and `/trials/`.

Filters: `team_id`. Ordering: `id`, `subject_name`, `team`. Search: `search=` over name/description.

Response fields: `id`, `subject_name`, `description`, `team_id`.

```bash
curl -s "https://api.brain-regeneration.com/subjects/?format=json"
```

Then use the id:

```bash
# Articles for the "Alzheimer's Disease" subject (id 13)
curl -s "https://api.brain-regeneration.com/articles/?subject_id=13&format=json"
```

## Categories — `GET /categories/`

A *category* is a team-curated grouping of articles/trials (~96 exist). Use it via
`category_slug` or `category_id` on `/articles/` and `/trials/`.

Filters: `category_id`, `team_id`, `subject_id`, `category_terms` (matches a keyword term),
plus `search=` over name/description. Single category: `GET /categories/{id}/`.

Response fields: `id`, `category_name`, `category_slug`, `category_description`,
`category_terms`, `article_count_total`, `trials_count_total`, `authors_count`, `top_authors`,
`monthly_counts`.

```bash
BASE="https://api.brain-regeneration.com"

# Browse categories for a subject
curl -s "$BASE/categories/?subject_id=1&format=json"

# Articles in a category by slug
curl -s "$BASE/articles/?category_slug=natalizumab&format=json"
```
