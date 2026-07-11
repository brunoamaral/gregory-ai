# Boolean `search=` syntax

The `search=` parameter on `/articles/` and `/trials/` parses a boolean expression and matches
it against the **title and abstract/summary** (an OR across both fields per term).

## Operators

| Syntax | Meaning | Example |
|---|---|---|
| `a b` (space) | **AND** — all terms must appear | `stem cells regeneration` |
| `a OR b` | either term (OR must be uppercase) | `alzheimer OR parkinson` |
| `AND` | explicit AND (uppercase; usually optional) | `microglia AND inflammation` |
| `-a` or `NOT a` | exclude term | `covid -vaccine` |
| `"…"` | exact contiguous phrase | `"multiple sclerosis"` |
| `( … )` | group sub-expressions | `(microglia OR astrocyte) AND inflammation` |

## Notes & limits

- A single bare term is a plain substring match — the simplest and most common case.
- Matching is **case-insensitive**.
- Limits: up to **16 terms** and **8 levels** of parenthesis nesting. Beyond that the parser
  degrades gracefully (it never errors — worst case it treats the whole string as one phrase).
- The query must be **URL-encoded**: space → `%20` or `+`, `"` → `%22`, `(` → `%28`, `)` → `%29`.

## Encoded examples

```bash
BASE="https://api.brain-regeneration.com"

# "multiple sclerosis" AND (remyelination OR myelin)
curl -s "$BASE/articles/?search=%22multiple%20sclerosis%22%20AND%20(remyelination%20OR%20myelin)&format=json"

# microglia, excluding review articles mentioning "review"
curl -s "$BASE/articles/?search=microglia%20-review&format=json"

# either neurodegenerative disease
curl -s "$BASE/trials/?search=alzheimer%20OR%20parkinson&format=json"
```

## When you need field-specific matching

- `title=` / `summary=` (articles & trials) match one field only.
- For structured filters (DOI, registry IDs, dates, sponsor, condition, status) use the dedicated
  params in [articles.md](articles.md) / [trials.md](trials.md) — they are indexed and faster than `search=`.
