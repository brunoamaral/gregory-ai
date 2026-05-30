# v24 Deployment Runbook

Two high-risk changes drive this runbook: **Postgres 15 → 17** (major upgrade)
and **74 migrations** (irreversible data migrations + write-blocking index build).
Plan a **maintenance window** with writes paused.

Read [`MIGRATION_SAFETY.md`](./MIGRATION_SAFETY.md) before starting.

---

## 0. Pre-flight (do not skip)

- [ ] Announce maintenance window; pause cron (`pipeline`, `send_weekly_summary`,
      `send_admin_summary`) so nothing writes mid-migration.
- [ ] Confirm target version/SHA and tag it (`v24`).
- [ ] **Run the upgrade helper (read-only) on each target DB:**
      ```bash
      docker exec gregory python manage.py prepare_v24_upgrade
      ```
      It reports the **critical** takeaways gate and the **moderate** index-lock
      table sizes, and tells you the exact next command. Possible outcomes:
  - *"Legacy columns already removed … Nothing to back up"* → gate clear, go to step 1.
  - *"Columns exist but hold no data"* → gate clear, go to step 1.
  - *"⚠ N row(s) carry legacy data"* → **back them up between 0045 and 0048**
      (the per-org tables don't exist until `gregory/0045`, and `0048` drops the
      legacy columns). Use the split-migration flow below.
- [ ] **If the helper flags pending legacy data, split the migration** so the
      backfill runs after the per-org tables exist but before the drop:
      ```bash
      # 1. create per-org tables, stop before 0048 drops the legacy columns
      docker exec gregory python manage.py migrate gregory 0047
      # 2. back up legacy data into the per-org tables (idempotent; fills empty
      #    fields on existing rows). Preview with --dry-run first.
      docker exec -it gregory python manage.py prepare_v24_upgrade --backfill --org-id <id>
      # 3. (the full `migrate` in step 3 then applies 0048 + everything else)
      ```
      This copies `articles.takeaways` / `summary_plain_english` and
      `trials.summary_plain_english` into `ArticleOrgContent` / `TrialOrgContent`
      so migration `0048` cannot drop live data.
- [ ] Verify `requirements.txt` installs cleanly on Python 3.12 in a scratch build
      (`pyproject.toml` was removed).

## 1. Backup

- [ ] Full logical dump from the **current PG15** instance:
      `pg_dump -Fc -d <db> -f gregory_pre_v24_$(date +%F).dump`
- [ ] Snapshot the droplet/volume as well (belt and suspenders).
- [ ] Verify the dump restores into a throwaway PG17 container (this doubles as the
      staging dry-run base, step 2).

## 2. Staging dry-run (mandatory — this is where 74 migrations get proven)

- [ ] Spin up **PG17** + the v24 image against the restored prod copy.
- [ ] `python manage.py migrate` — time it, watch for lock waits on the
      `0022` index build and the field-removal migrations.
- [ ] `python manage.py createcachetable gregory_cache`
- [ ] Smoke test:
  - [ ] API: search, CSV export, `/stats/`, a private-org key, a public read.
  - [ ] Admin: announcements (CKEditor image upload), per-org content inlines.
  - [ ] Emails: `send_weekly_summary` + `send_admin_summary` to a test list
        (check unsubscribe links use the API domain, images render).
  - [ ] `pipeline` end-to-end (ingest → ML → categories).
  - [ ] Per-org takeaways visible where legacy ones used to be.
- [ ] Confirm no data loss: re-run the takeaways counts from step 0.

## 3. Production deploy

- [ ] Enter maintenance window; stop the app container (writers quiesced).
- [ ] Take a final dump (state immediately pre-migrate).
- [ ] **Postgres 15 → 17 upgrade** (dump/restore path):
      - [ ] Stand up PG17, restore the final dump, repoint `DB_HOST`.
      - [ ] (compose already pins `postgres:17`.)
- [ ] Pull/​build the v24 image (Python 3.12, multi-arch).
- [ ] `python manage.py migrate`
- [ ] `python manage.py createcachetable gregory_cache`
- [ ] `python manage.py collectstatic --noinput` if static changed.
- [ ] Start the app; health-check API + admin login.

## 4. Post-deploy verification

- [ ] Re-run the step-2 smoke tests against production.
- [ ] Re-enable cron jobs; confirm `pipeline` lock file is clear.
- [ ] Watch logs for one full pipeline + one summary send.
- [ ] Confirm `DEBUG` is off in prod (`DJANGO_DEBUG` unset / settings `DEBUG = False`).

## 5. Publish the release

- [ ] Push the `v24` tag.
- [ ] Create the GitHub Release from `RELEASE_NOTES.md`, mark it **Latest**
      (this moves the "Latest" label from v23 to v24 automatically).

---

## Rollback

Several migrations are irreversible (field/table drops, `noop`-reverse backfills),
so **`migrate` is not cleanly reversible**. Rollback = **restore the pre-migrate
dump** into PG15 and redeploy v23:

- [ ] Stop v24 app.
- [ ] Restore `gregory_pre_v24_*.dump` into the PG15 instance.
- [ ] Redeploy the v23 image.
- [ ] Re-enable cron.

Decision point: if `migrate` fails partway, do **not** try to hand-fix forward —
restore the dump. The window's backup is the rollback plan.
