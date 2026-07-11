# RSS feeds

Two public RSS/Atom feeds. Fetch them like any URL; they return XML, not JSON (no `format`
param). Useful for subscribing to new content or polling for updates.

## Articles by author — `GET /feed/author/{orcid}/`

New articles from a specific author. The path segment accepts **either** an ORCID iD **or** a
numeric `author_id`.

```bash
# By ORCID
curl -s "https://api.brain-regeneration.com/feed/author/0000-0002-1825-0097/"

# By numeric author id
curl -s "https://api.brain-regeneration.com/feed/author/547318/"
```

Find the author first with [authors.md](authors.md).

## Trials by subject — `GET /feed/trials/subject/{subject_slug}/`

New clinical trials for a research subject. The path uses the subject **slug** — the lower-cased,
hyphenated form of the subject name (e.g. "Multiple Sclerosis" → `multiple-sclerosis`).

```bash
curl -s "https://api.brain-regeneration.com/feed/trials/subject/multiple-sclerosis/"
```

Look up available subjects and their names in [subjects-and-categories.md](subjects-and-categories.md),
then slugify the name (lowercase, spaces and punctuation → hyphens).
