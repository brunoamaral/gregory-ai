# Article DOI de-duplication

## The bug

Two independently-created `Articles` rows could silently converge on the same
DOI. The feedreader creates a DOI-less row for a paper it can't match at ingest
time — typically a `BASE for …` entry whose title differs only in punctuation or
whitespace, so `find_existing_article`'s DOI / link / title lookups all miss.
Weeks later `find_doi` runs a CrossRef title search and does
`article.doi = doi; article.save()` with **no check** for whether another row
already holds that DOI. Two rows for one paper, converging quietly, with nothing
in the pipeline noticing.

Each duplicate carries independent relations (sources, subjects, teams, authors,
ML predictions, category assignments, trial references, org content, sent-
notification history), so they can't just be deleted — they must be merged.

## The fix (three layers)

1. **Prevention at the write site.** `find_doi` and the feedreader now route DOI
   assignment through `gregory.services.article_merge.assign_doi_or_merge`, which
   merges into the existing holder instead of creating a collision.
   `find_existing_article` matches DOIs case-insensitively so a case-only variant
   resolves to the existing row.

2. **A database backstop.** `unique_article_doi` — a partial unique index on
   `Lower(doi)` where the DOI is non-null and non-empty (migration 0075). This is
   what survives any *future* code path that forgets the application-level guard.
   Unlike `title`, which is deliberately non-unique, a DOI identifies exactly one
   paper.

3. **A weekly report.** `check_duplicate_dois` exits non-zero if any DOI is held
   by more than one article. Should always be zero once the constraint exists;
   catches data entering outside the ORM (raw-SQL migrations, restores).

## Rollout order (important)

The constraint cannot be applied while duplicates exist, so **clean up first**.
Migration 0075 has a pre-flight check that refuses to apply (with the exact
commands to run) if any duplicate DOIs remain — so a premature `migrate` fails
loudly and safely rather than half-applying.

```bash
# 1. Preview every same-DOI duplicate group (dry run — rolls back):
docker exec gregory python manage.py merge_duplicate_articles --scan

# 2. Commit the merges:
docker exec gregory python manage.py merge_duplicate_articles --scan --commit

# 3. Now the constraint applies cleanly:
docker exec gregory python manage.py migrate gregory 0075

# 4. Confirm clean:
docker exec gregory python manage.py check_duplicate_dois
```

Preprint-vs-published pairs (same paper, *different* DOIs) can't be found by
`--scan`. Merge them explicitly:

```bash
docker exec gregory python manage.py merge_duplicate_articles \
    --keep <published_id> --remove <preprint_id> --commit
```

## Survivor selection

Per group the survivor is chosen to preserve curation, in order: has a manual
relevance decision → has ML predictions → earliest `discovery_date` → lowest id.
The survivor's `relevant` flag is recomputed from the unioned predictions and
manual decisions rather than copied blindly.

## Suggested cron

```cron
# Weekly duplicate-DOI report (Mondays 07:00)
0 7 * * 1 docker exec gregory python manage.py check_duplicate_dois
```
