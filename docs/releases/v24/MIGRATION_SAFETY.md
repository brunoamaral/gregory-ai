# v24 Migration Safety Review

Scope: every new migration on `main` since the **v23** tag (`cec3d65`, 2025-06-21).

- **74 new migrations** — gregory 36, subscriptions 27, sitesettings 9, api 2.
- **9 data migrations** (`RunPython`), **4 raw-SQL** (`RunSQL`), **10 field/model removals**.

Run order is enforced by Django's dependency graph, so a single `migrate` applies
everything in the correct sequence. The risks below are about **data loss**,
**locking**, and **reversibility**, not ordering.

---

## 🔴 Critical — irreversible data loss if prerequisite was skipped

### `gregory/0048_remove_legacy_takeaway_fields.py`
Drops `Articles.takeaways`, `Articles.summary_plain_english`,
`Trials.summary_plain_english` (+ the matching historical fields).

- The migration is a **pure schema removal** — no data copy, no guard.
- The data was meant to be moved into `ArticleOrgContent` first by the
  `migrate_legacy_takeaways` management command (added in Phase 7, PR #650).
- **That command was deleted** in commit `d482dfec` with the note
  *"one-shot, done"* — i.e. it had already been run on the author's production.
- **`ArticleOrgContent` (created in `0045`) is schema-only — there is no
  data migration that copies the legacy columns into it.**

**Implication:**
- On **the production where the one-shot already ran** → safe, data already in `ArticleOrgContent`.
- On **any instance that never ran `migrate_legacy_takeaways`** (fresh org
  instance, a DB restored from before Phase 7, gregory-002, etc.) → applying
  `0048` **permanently drops the takeaways/summaries with no in-tree recovery path.**

**Action before deploy:** For each target DB, confirm `ArticleOrgContent` is
populated **before** `0048` runs. If not, restore the deleted command from
`git show b0973f5a:django/gregory/management/commands/migrate_legacy_takeaways.py`
and run it first. See the runbook pre-flight gate.

---

## 🟠 Moderate — locking / write-blocking during migrate

### `gregory/0022_add_category_performance_indexes.py`
- `atomic = False` but uses **plain `CREATE INDEX ... IF NOT EXISTS`**, *not*
  `CREATE INDEX CONCURRENTLY` (the module docstring says so explicitly).
- Plain `CREATE INDEX` takes a lock that **blocks writes** on each target table
  (`articles_team_categories`, `articles_authors`, `articles`, `trials`, …) for
  the duration of the build.
- On large `articles`/`trials` tables this can be a multi-second to multi-minute
  write stall. **Fine inside a maintenance window; risky on a live writer.**

### `gregory/0040_remove_teamcredentials.py` · `subscriptions/0008` · `subscriptions/0010_switch_subscriptions_to_through_model.py`
- `DeleteModel` / through-model switch rewrite table structure. Backed by
  backfill migrations (`subscriptions/0009`, `0013`) that must succeed first.
- Verify the `0009`/`0013` backfills completed (they have reverse ops) — a
  partial backfill before the structural change loses subscription links.

---

## 🟡 Low — irreversible but non-destructive

`RunPython` migrations with **no reverse** (`migrations.RunPython.noop` only, or
none). Forward-safe; a rollback past them silently does nothing (won't restore
prior values). Acceptable for one-shot backfills, listed for completeness:

| Migration | What it backfills |
|---|---|
| `gregory/0017_populate_full_name_field` | Author `full_name` |
| `gregory/0026_normalize_orcid_values` | ORCID normalization (no reverse) |
| `gregory/0043_organizationapisettings` | Org API settings |
| `sitesettings/0006_backfill_credentials_from_team` | Team → CustomSetting creds |
| `sitesettings/0010_copy_allowed_domains_from_lists` | Lists → CustomSetting domains |
| `subscriptions/0009 / 0013` | Unsubscribe tokens + list subscriptions |
| `subscriptions/0027_backfill_announcement_organization` | Announcement → org |
| `api/0004_apiaccessscheme_organization_required` | API access org flag |

`allowed_domains` path is two-step and order-dependent:
`sitesettings/0010` **copies** Lists→CustomSetting, then
`subscriptions/0020` **removes** `Lists.allowed_domains`. The copy depends-on
correctly precedes the removal — safe as a single `migrate` run, **but do not
run the apps' migrations separately/out of order.**

### `gregory/0047_drop_authtoken_tables.py`
Raw SQL `DROP TABLE` for the DRF authtoken tables, `reverse_sql=noop`.
Irreversible but intended (the Token endpoint was removed). No app data lost.

---

## Helper command

`gregory/management/commands/prepare_v24_upgrade.py` automates the critical and
moderate checks above:

- **Default (read-only):** reports the takeaways gate, how much legacy data is
  pending, and the sizes of the tables `0022` will lock.
- **`--backfill --org-id <id>`:** idempotently copies legacy
  takeaways/summaries into `ArticleOrgContent` / `TrialOrgContent` so `0048`
  can't drop live data. Reads the legacy columns via raw SQL; for existing
  per-org rows it fills only the fields that are currently empty (safe to
  re-run). Supports `--dry-run` and `--noinput`.

The per-org tables are created by `0045` and the legacy columns dropped by
`0048`, so the backfill must run **between** them via a split migration:

```bash
docker exec gregory python manage.py prepare_v24_upgrade            # 0. check
docker exec gregory python manage.py migrate gregory 0047          # 1. create per-org tables
docker exec -it gregory python manage.py prepare_v24_upgrade --backfill --org-id 3  # 2. back up
docker exec gregory python manage.py migrate                       # 3. apply 0048 + rest
```

The command detects this state and refuses to back up (with guidance) if the
per-org tables don't exist yet, rather than erroring on a missing table.

## Summary checklist

- [ ] **Gate:** run `prepare_v24_upgrade`; back up legacy data if it flags pending rows.
- [ ] Run `migrate` inside a **maintenance window** (index build in `0022` blocks writes).
- [ ] Apply **all apps together** in one `migrate` (cross-app order matters: allowed_domains, subscriptions backfills).
- [ ] Take a **full DB backup** immediately before `migrate` (several steps are irreversible).
- [ ] After `migrate`, run `createcachetable gregory_cache` (new DB cache backend).
