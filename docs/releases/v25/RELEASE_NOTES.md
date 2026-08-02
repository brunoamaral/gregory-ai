# Gregory AI v25

_Range: v24 (2026-05-30) → main (2026-07-30). 134 merged PRs, 563 commits,
55 migrations._

The data-quality release. Clinical trials stop being a thin wrapper over
whatever each registry happened to send and become normalised, deduplicated,
canonicalised records: sponsors, countries, sites, phase, recruitment status,
study type and sex eligibility all now have derived canonical fields backed by
their raw source values. Alongside that, a sustained push on API performance
after several production stalls, and a full audit of the newsletter system that
turned up three failures already running in production.

---

## ⚠️ Breaking changes — read before upgrading

- **Deprecated team-scoped endpoints removed** (#750). Clients still calling them must move to the org-scoped equivalents.
- **Trial contact fields no longer exposed via the API** (#786). `ethics_review_contact_*` and related fields were withdrawn from serialisers.
- **`TeamSerializer` now whitelists fields** instead of exposing `__all__` (#761). Consumers relying on undocumented fields will see them disappear.
- **Author ORCID stored as a bare ID**, not a URL (#775). `https://orcid.org/0000-…` became `0000-…`. Any consumer string-matching the full URL needs updating.
- **`Articles.access` NULL normalised to `"unknown"`** (#762), and normalised at write time from then on.

### New deployment requirements

- **Two new environment variables**, required for the Postmark webhook to authenticate. Without them the endpoint rejects everything with 403 — which is fail-safe, but silent:
  ```
  POSTMARK_WEBHOOK_USERNAME=
  POSTMARK_WEBHOOK_PASSWORD=
  ```
  Configure the Postmark webhook URL as
  `https://<user>:<pass>@<your-domain>/webhooks/`, on the **broadcast** stream.
- **New cron entry.** Announcements are no longer sent inside the admin request; the admin queues them and a command sends them:
  ```cron
  */5 * * * * docker exec gregory python manage.py send_announcement
  ```
  Without this, queued announcements never go out.
- **55 migrations**, 46 of them in `gregory`. Several are data backfills over the trials table — review timing against table size.

---

## ✨ Highlights

### Clinical trials — normalisation and identity

The largest strand of the release (~42 PRs).

- **New models**: `Sponsor`, `SponsorAlias`, `SponsorMergeCandidate`, `TrialCountry`, `TrialSite`.
- **Derived canonical fields** alongside the raw registry values: `phase`, `recruitment_status_normalized`, `study_type_normalized`, `inclusion_gender_normalized`, `regions_normalized`, plus canonical age bounds in years. Raw values are preserved; the normalised field is recomputed on save.
- **Sponsor canonicalisation** with punctuation-insensitive alias keys and a review queue for probable duplicates, exposed through a `/sponsors/` endpoint, nested objects, filters and facets.
- **Trial sites** captured from both ClinicalTrials.gov and CTIS, including city, state and coordinates, exposed detail-only plus a flat `/trials/sites/` endpoint.
- **Identity fixes**: canonical identifier extraction for matching, an end to false merges and link flip-flop, and a stop to link overwrites when multiple sources describe the same trial.
- **Ingestion**: a CTIS public-API feedreader, CTIS retrieve enrichment (all-countries, recruitment dates, eligibility), ICTRP and CTGov Tier-1 field gap-filling, HTML stripped from WHO ICTRP fields at ingest, and raw inbound streams captured to JSONL for replay.
- **API**: multi-select status and phase filters, numeric age-eligibility filtering, multi-value country filter indexed via `EXISTS`, recruiting-first ordering, stats facets, XLSX export.

### API performance

Largely reactive to production stalls, and measured rather than guessed.

- `/authors/` N+1 eliminated, and a separate fix for runaway CPU on its pagination `COUNT(*)`.
- Categories `monthly_counts` N+1: **17.5s → 1.7s**.
- Org-visibility join + `DISTINCT` replaced with an `EXISTS` subquery.
- Category count fan-out that was stalling production removed.
- Search views: N+1 prefetch and a `DISTINCT`-pagination trap fixed.
- Trigram index for `?orcid=` filtering.
- **True streaming** for CSV bulk exports, replacing a fake-streaming path that buffered the whole response.
- `CONN_MAX_AGE` connection reuse; cached `/trials/stats/` and `/articles/stats/`.

### Search and filtering

- A **boolean `?search=` parser** supporting OR, AND, negation and phrases — plus the follow-ups needed to stop DRF's `SearchFilter` undoing it.
- `subjects_any` OR semantics for articles and trials, date-range filters, comma-separated DOI lists, sort by AI relevance (`ml_score`).
- **POST-body filters now honoured** on the search endpoints; previously everything except a handful of keys was silently dropped, returning a wrong `200` rather than an error.

### Machine learning

- Training-data text preparation and per-subject labels corrected.
- Model loading, version resolution and batching fixed in `predict_articles`; BERT `max_len` default and saved-architecture restore; LSTM vectoriser load.
- **Off-box training**: dataset export plus `train_models --dataset-file`, with documented TensorFlow version parity requirements.
- **Relevance now uses only the latest prediction** per (article, subject, algorithm), so a retired model version can no longer keep an article relevant forever after a retrain.

### Articles and authors

- Retraction status tracked from CrossRef, with error handling for lookup failures.
- `pdf_link` backfilled via Unpaywall, with resumable logging and CSV reporting.
- First-seen-wins canonical link with multi-source merge; duplicate articles converging on the same DOI merged behind a uniqueness constraint.
- HTML tags and whitespace cleaned from feed article titles.
- Authors: ORCID Tier-1 enrichment, biography capture with manual recheck, a co-authors endpoint with relevant-article counts, and an admin bulk-merge action.

### Subscriptions and email

A full audit in the last week of July found three failures already running in
production, plus ten further defects. All are fixed.

- **Trial notification emails exceeding Postmark's 5 MB body limit** — 413 consecutive failures over 15 days, self-perpetuating because a failed send wrote no dedup records. Fixed with per-email content caps, a staleness filter measured on the trial's own registration date rather than when it was discovered, and a size backstop that shrinks and re-renders.
- **"Unsubscribe from all lists on this site" had never worked** — it filtered on the wrong foreign key, matched nothing, and still showed a success page. Recorded as a data-protection incident; affected individuals could not be identified because access-log retention did not reach far enough back.
- **Suppressed recipients retried indefinitely** — one address 210 times. Postmark responses are now parsed centrally and a 406 deactivates the subscriber.
- **A new Postmark suppression/reactivation webhook** at `POST /webhooks/`, with a `SuppressionEvent` audit trail recording exactly which subscriptions each suppression deactivated — which is what makes reactivation possible at all. Hard bounces and manual suppressions auto-restore within 12 months; spam complaints never do.
- Announcements are now idempotent and resumable, and queue to a management command instead of sending inside the admin request. Retrying a partially-failed announcement no longer re-mails everyone who already received it.
- Content organizer: trial recruiting status keyed on the normalised field instead of a substring match that misclassified ~59% of trials; the invisible featured/regular article split removed for digests and made visible for admin summaries; Latest Research now a true delta.

### Categories

Per-category match configuration replacing a global setting, diff-based rebuild
with provenance tracking, deduplicated monthly relevant-article counts, and a
curated intervention-modality grouping.

### Infrastructure and CI

- CI test suite sped up (skip migrations, fast hasher, in-memory cache) and refactored for `setUpTestData`.
- Build and deploy gated on the Tests workflow passing.
- **Database-aware Django system checks** added after an over-length index name broke a production deploy — `manage.py check` alone skips those checks, so neither existing gate caught it.
- Ruff empty-catch gate; `logging` in place of `print`; entrypoint automating migrate and collectstatic; simplified docker compose.

---

## Upgrade

1. Set `POSTMARK_WEBHOOK_USERNAME` and `POSTMARK_WEBHOOK_PASSWORD`, and configure the Postmark webhook on the broadcast stream.
2. Add the `send_announcement` cron entry.
3. Run migrations. Review the trials backfills against your table size before starting.
4. Audit any API client for the breaking changes above — particularly the bare-ID ORCID format and the removed team-scoped endpoints.

## Known gaps

Carried forward deliberately, none failing in production:

- `Articles.ml_score` and `Articles.relevant` are maintained by a `post_save` signal that the pipeline's `bulk_create` bypasses, so both drift between manual backfills. Plan: [ml-prediction-signal-bypass-plan.md](../../ml-prediction-signal-bypass-plan.md).
- The ML consensus rule has three implementations bound only by a comment. They agree today; nothing enforces that.
- Postmark suppression is reactive as well as webhook-driven — the reactive path stays as a backstop, because Subscription Change events are only retried for ~21 minutes.
