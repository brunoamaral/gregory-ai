# Plan: fix the orphaned `articles_title_*_like` index that breaks from-scratch builds

**Status: implemented and verified.** Fix shipped in `fix/migration-0073-orphan-like-index`.

## Symptom

`migrate` against an empty database dies on `gregory.0095`:

```
django.db.utils.ProgrammingError: relation "articles_title_ed7ced3d_like" already exists
```

Everything before 0095 applies cleanly; the chain stops there. This breaks CI (if it ever
ran migrations), fresh clones, and `python manage.py test` — Django's own runner builds
`test_<db>` through the real migration chain. It does **not** affect dev or House, which are
long-lived databases that were already past this point.

## Reproduced

Empty database, current branch, full chain:

```bash
docker exec db psql -U gregory -d postgres -c 'CREATE DATABASE scratch_migcheck OWNER gregory;'
docker exec -e POSTGRES_DB=scratch_migcheck gregory python manage.py migrate
```

Fails at `0095_index_title_and_discovery_date_ordering_fields`. Index state at that point:

```
articles_title_ed7ced3d_like | CREATE INDEX ... ON articles USING btree (title text_pattern_ops)
```

Dropping that one index and re-running `migrate` carries the whole chain — gregory, api,
subscriptions, sitesettings — to completion. So the orphan is the only blocker.

## Root cause

On PostgreSQL, Django creates **two** database objects for an indexed or unique
`TextField`/`CharField`: the index/constraint itself, plus a companion
`<table>_<column>_<hash>_like` index with `text_pattern_ops` (used by `LIKE 'foo%'`).
`_create_like_index_sql` adds it; `_alter_field` in the postgres schema editor is what
normally drops it again when the field stops being indexed.

- `0001_initial` declares `articles.title = TextField(unique=True)` → Postgres gets
  `articles_title_key` (unique) **and** `articles_title_ed7ced3d_like`.
- `0073` drops the uniqueness through `SeparateDatabaseAndState`, because on the legacy
  dev/House schema the unique existed as a raw index rather than a table constraint and the
  generated `ALTER TABLE ... DROP CONSTRAINT` failed there (see commit 36af9dc3). Its raw SQL
  drops `articles_title_key` in both forms — but **not** the `_like` companion, which
  Django's own `AlterField` would have removed. On a fresh database the `_like` index
  survives with nothing referencing it.
- `0095` sets `db_index=True` on `articles.title`, so `_alter_field` issues
  `CREATE INDEX articles_title_ed7ced3d_like ...` again. The name is a deterministic hash of
  `(articles, title, _like)`, so it collides with the orphan.

dev and House never had the orphan: their schema predates the migration graph, so
`0001_initial`'s `_like` index was never created there. That is exactly why 0095 applied
cleanly on dev (2026-08-20 12:10) and why the bug only shows up on a build from zero.

`trials.title` had the same `unique=True` origin but was cleared in `0050` with a plain
`AlterField`, which dropped its `_like` companion correctly — no orphan there. Same for
`historicalarticles.title` and `historicaltrials.title`. `articles.title` is the only one.

## Fix

Two small edits. The first is the root cause; the second makes any database already sitting
between 0073 and 0095 self-heal instead of needing the manual `DROP INDEX`.

### 1. `gregory/migrations/0073_...` — drop the companion index too

Add one statement to the existing `RunSQL`, so the raw SQL does everything Django's
`AlterField` would have done:

```python
sql=[
    'ALTER TABLE articles DROP CONSTRAINT IF EXISTS articles_title_key;',
    'DROP INDEX IF EXISTS articles_title_key;',
    # Postgres-specific companion index Django creates alongside a unique/indexed
    # text column (text_pattern_ops, for LIKE 'foo%'). AlterField would drop it
    # automatically; the raw SQL above has to do it explicitly. Name is Django's
    # deterministic _create_index_name('articles', ['title'], suffix='_like').
    'DROP INDEX IF EXISTS articles_title_ed7ced3d_like;',
],
```

Editing an already-applied migration is safe here: nothing is renamed or reordered, the
operation list is unchanged, and Django never re-runs an applied migration — so dev and
House are untouched. It only changes what a *fresh* build does.

### 2. `gregory/migrations/0095_...` — drop it defensively before recreating

Prepend as the first operation:

```python
migrations.RunSQL(
    # Databases that applied 0073 before it learned to drop this index still carry
    # it; the AlterFields below recreate it. Harmless no-op everywhere else.
    sql='DROP INDEX IF EXISTS articles_title_ed7ced3d_like;',
    reverse_sql=migrations.RunSQL.noop,
),
```

Safe on House, where 0095 has not been deployed: the index isn't there, `IF EXISTS` makes it
a no-op, and the `AlterField` immediately after creates both indexes normally. Reversing 0095
still drops both via `AlterField`, so the noop reverse leaves correct state.

### 3. Verify — done

All three passed on the merged `main` tree:

- Half-migrated database stuck at 0095 (the pre-fix state): resumes and applies 0095 cleanly.
- Empty database: the full chain — gregory, api, subscriptions, sitesettings — applies end to end.
- Both databases end with byte-identical index sets, and `makemigrations --check --dry-run` reports no drift.
- `python manage.py test gregory.tests.test_article_links` builds `test_gregorybackoffice` through the real chain and passes (7 tests) — the reported symptom.

Commands used:

```bash
docker exec db psql -U gregory -d postgres -c 'DROP DATABASE IF EXISTS scratch_migcheck;' -c 'CREATE DATABASE scratch_migcheck OWNER gregory;'
docker exec -e POSTGRES_DB=scratch_migcheck gregory python manage.py migrate
docker exec -e POSTGRES_DB=scratch_migcheck gregory python manage.py makemigrations --check --dry-run
docker exec db psql -U gregory -d postgres -c 'DROP DATABASE IF EXISTS scratch_migcheck;'
```

Then `python manage.py test` (or `pytest`) locally.

## Prevention — the actual gap

Nothing in CI has ever run the migration chain:

- `pytest.ini` sets `addopts = --reuse-db --nomigrations`, so pytest builds the test schema
  straight from model state and skips every migration. `conftest.py` documents this and
  hand-recreates the pieces (`CREATE EXTENSION pg_trgm`, the 0022 raw indexes) that only
  exist as `RunSQL`.
- `.github/workflows/tests.yaml` runs `pytest`, `manage.py check --database default`, and
  `makemigrations --check --dry-run`. None of those apply a migration. `makemigrations
  --check` compares *model state* to migration state, so a migration that is broken only
  against a real database passes it.

Add one step to the `pytest` job in `.github/workflows/tests.yaml`, after "Generate ephemeral
keys" and before "Django system checks":

```yaml
      - name: Migrations apply to an empty database
        working-directory: django
        # pytest runs with --nomigrations (pytest.ini), so the migration chain is
        # never exercised there and a migration that only works against an
        # already-migrated database ships green. This is the gate for that.
        run: python manage.py migrate
```

It targets the empty `gregory_test` service database; pytest uses the `test_`-prefixed one,
so the two don't collide in either order. `CREATE EXTENSION pg_trgm` works because the CI
Postgres runs as `postgres` under trust auth. Cost is a couple of minutes on an empty schema.

Optionally worth a line in `CLAUDE.md`: `pytest` is the fast local runner (no migrations),
`manage.py test` is the one that exercises the chain.

## Out of scope, noted

Comparing a from-scratch schema against dev shows wider drift — dev is missing a number of
Django-generated indexes (`auth_*` uniques, some FK indexes) and carries differently-named
equivalents of others (`articles_title_link_21ea7598_uniq` vs `unique_article_title_link`,
`authors_ORCID_2be13733_uniq` vs `authors_ORCID_key`). That is the legacy schema showing
through and is not what breaks the build; the cases spot-checked are covered by hand-made
equivalents. Worth a separate schema-reconciliation pass, not this one.
