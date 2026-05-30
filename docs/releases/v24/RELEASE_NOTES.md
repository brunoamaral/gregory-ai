# Gregory AI v24

_Range: v23 (2025-06-21) → main (2026-05-30). 113 merged PRs, ~717 commits._

The multi-organization release: Gregory now runs as a true multi-tenant platform —
API keys, ORCID credentials, content, and visibility are all scoped per
organization. Plus a new email Announcements system, a much faster and search-capable
API, new data sources (BioRxiv, MedRxiv, ClinicalTrials.gov, PNAS), and an
infrastructure jump to Postgres 17 and Python 3.12.

---

## ⚠️ Breaking changes — read before upgrading

- **Postgres 15 → 17.** Major version bump; requires a dump/restore or `pg_upgrade`,
  not an in-place restart. See the deployment runbook.
- **Python 3.12.** Rebuild the image; `pyproject.toml` was removed — deploys install
  from `requirements.txt`.
- **API is now read-only.** All ModelViewSets are locked to read-only (Phase 5).
  External clients that POST/PUT must move to the org-scoped write path.
- **DRF Token auth removed.** The `/api-token-auth/` endpoint and authtoken tables
  are gone (Phase 6). Use JWT or per-organization API keys instead.
- **Legacy article fields dropped.** `Articles.takeaways` and
  `summary_plain_english` now live in `ArticleOrgContent` (per-org). ⚠️ See the
  migration-safety note — confirm data was moved before deploying.
- **`Team.organization` is now NOT NULL.** Defensive null-handling removed; every
  team must belong to an organization.
- **`allowed_domains` moved** from `Lists` to `CustomSetting`.
- **`TeamCredentials` model removed** — ORCID/credentials are now per organization/team.
- New DB cache backend: run `python manage.py createcachetable gregory_cache` after migrate.

---

## ✨ Highlights

### Multi-organization / multi-site
- API keys per organization and team; org-scoped POST enforcement.
- Per-organization ORCID credentials (env-var dependency removed).
- Flag to mark an organization's API **private**, with visibility middleware.
- Multi-site support for subscription lists.

### API
- Full-text **search** endpoint (articles, trials, authors) with performance tuning.
- **CSV export** + streaming responses for large downloads; full-results search export.
- New **/stats/** aggregate endpoint with org filter, reduced joins, and caching.
- Author endpoints: sort by article count, author statistics on `/categories/`.
- New filters: intersection, `has_clinical_trials` (detects NCT IDs in abstracts),
  case-insensitive trial status, ML-score threshold, date filters.
- Per-org takeaways/summaries in serializers; read-only viewsets; Token endpoint removed.

### Email & subscriptions
- **Announcements**: CKEditor-authored emails with CTA buttons and inline images,
  duplicate action, per-org ownership, image-quality + host-config safeguards.
- Weekly digest: per-date or per-relevancy modes, article limits, threshold filtering.
- Subscriber analytics: list distribution, historical active-subscriber chart.
- Dark-mode / Outlook email rendering fixes; sender-name setting; API-domain
  unsubscribe links; import / reconcile / prune subscriber commands.

### Data sources & ML
- **BioRxiv & MedRxiv** support; **ClinicalTrials.gov API**; **PNAS** RSS.
- New RSS feeds for articles and clinical-trials-by-subject.
- Better ML algorithms and category matching with batch processing.

### Admin & ops
- Richer admin: sources, subjects (with content analytics), teams (categories,
  sources, subjects inlines), per-org editorial inlines, subject deletion.
- Multi-arch Docker build & push; Makefile build commands.
- DB pruning for notifications/history; orphan author/article cleanup commands.
- Postgres 17, Python 3.12, codebase cleanup.

---

## Upgrade

Follow [`DEPLOYMENT_RUNBOOK.md`](./DEPLOYMENT_RUNBOOK.md). Review
[`MIGRATION_SAFETY.md`](./MIGRATION_SAFETY.md) first — there are 74 migrations
including irreversible data migrations and a Postgres major-version upgrade.
