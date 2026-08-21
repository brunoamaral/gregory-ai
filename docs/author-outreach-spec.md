# Spec: one-time author outreach email

Status: approved 2026-08-20 and implemented. This is the design record — the measured data
the feature was sized from, the decisions taken and why, and the alternatives rejected.
For how to operate the feature, see [author-outreach.md](author-outreach.md).

Decisions below were made by Bruno on 2026-08-20 unless marked "assumption".

## Goal

Send a single, personal email to the authors of research that a GregoryAI site is **about to
feature** in its research digest newsletter, telling them their paper has been selected for the
next edition and pointing them at their author profile page. One email per author per site,
ever.

The tense matters and drives the whole design: the email goes out **ahead of** the digest, not
after it. Eligibility is therefore read from the digest's own selection function, not from what
it has already sent.

This is cold outreach signed with a real person's name. Every design decision below is
subordinate to one constraint: **a spam complaint is more expensive than a missed send.**

## Measured reality

Numbers from the dev database on 2026-08-20. They set the scale of the whole feature.

| Measure | Value |
|:--------|:------|
| Authors in the database | 257,948 |
| Authors with any public ORCID email | 5,537 (2.1%) |
| Eligible authors for the **next** send, all four digests, full rule set | 2–3 |
| Eligible authors, retrospective 90-day window (back catalogue) | 17 |
| Eligible authors, retrospective 30-day window | 1 |
| Articles ever featured in a weekly digest | 1,547 |
| Featured articles whose `published_date` is within 7 days of the send | 0 |

Per-list, for the next send (`select_digest_articles`, never-sent candidates only, capped at
the list's `article_limit`):

| List | Sort | Candidates | Never sent | Eligible authors |
|:-----|:-----|-----------:|-----------:|-----------------:|
| MS Weekly Digest | relevancy | 12 | 5 | 2 |
| Neuroinflammation | date | 95 | 90 | 0 |
| Neuroimmune Interactions | date | 3 | 2 | 0 |
| Cell Reprogramming | date | 6 | 1 | 0 |

Three consequences:

- **Steady state is 0–3 emails per weekly run.** The three `article_sort_order="date"` digests
  contribute nothing, because the relevance gate below finds no ML predictions for their
  subjects. Everything currently flows from the MS digest.
- **A digest candidate is not the same as "will be featured".** `select_digest_articles` works
  over the list's `lookback_days` (30), so its candidate set includes articles featured weeks
  ago that no existing subscriber will receive again. Eligibility must additionally require
  that the article has **never** been sent for that list, and that it ranks within
  `article_limit` — otherwise the email promises an appearance that will not happen.
- **For the back-catalogue campaign, the recency window keys off `sent_at`, never
  `published_date`.** Zero of 1,547 ever-featured articles were published within 7 days of
  being featured — discovery lag from PubMed and the source feeds is weeks to months, so a
  `published_date` window sends nothing, permanently.

## Decisions

### Legal basis and consent

| Item | Decision |
|:-----|:---------|
| Basis | Legitimate interest (GDPR Art. 6(1)(f)), recorded per send, with the one-time nature of the contact noted in the record |
| Balancing test | Written once, stored in `docs/author-outreach.md`, referenced by the stored basis string |
| Postmark stream | **Broadcast**, not transactional. Broadcast carries List-Unsubscribe headers and its own suppression list; a complaint on the transactional stream would degrade every transactional message the account sends |
| Opt-out | A real "never contact me again" link, tokenised, in every email |
| Opt-out scope | Future email only. It does **not** hide, alter, or unpublish the author profile page |
| Complaint / hard bounce | Global do-not-contact across every site in the database |

### Who qualifies

Campaigns run in one of two **modes**. The steady-state campaign is `upcoming`; the
back-catalogue campaign is `retrospective`. Rules 3–8 are shared.

**Mode `upcoming` (the default, and the one the drafted copy is written for)**

1. The article is in the candidate set `select_digest_articles(list, list.lookback_days)`
   returns for a weekly digest list on the campaign's site — the same function the digest
   itself and the staff preview use, so outreach can never disagree with what the digest
   would pick.
2. The article has **no** `SentArticleNotification` row for that list (never sent to anyone),
   and ranks within `list.article_limit` by the digest's own priority score. Together these
   make "will be featured in the next edition" true rather than merely plausible.

**Mode `retrospective` (back catalogue only)**

1. A `SentArticleNotification` row exists for `(article, list)` on the campaign's site.
2. That send happened within `campaign.featured_within_days` of now.

**Shared rules**

3. The article passes a relevance gate for at least one of that list's subjects: ML consensus
   (per `Subject.ml_consensus_type`, scored at **`list.ml_threshold`**) **OR**
   `ArticleSubjectRelevance.is_relevant=True`. Union, not intersection. A subject for which
   the article is explicitly marked `is_relevant=False` cannot satisfy this rule, even when ML
   consensus passes for it — stricter than the digest, which only drops an article rejected
   across *all* its subjects. This gate is what removes the `date`-sorted digests, whose
   subjects have no ML predictions.
4. If the campaign names subjects, the list's subjects intersect them; an empty subject set
   means every weekly digest list on the site.
5. The author has a non-empty `Authors.emails`, `orcid_verified_email=True`, and
   `orcid_claimed=True`.
6. Their address is not in `AuthorContactOptOut`, not Postmark-suppressed, and does not
   belong to a `Subscribers` row that has been deactivated.
7. No `AuthorOutreach` row already exists for `(site, author)` in any status.
8. All authors on a qualifying paper are eligible — ORCID gives no reliable authorship
   position, so first/last/corresponding cannot be distinguished. Accepted.

Up to **3** papers are named in one email, most recent `published_date` first. One email per
author regardless of how many qualifying papers they have.

### Timing

**Superseded during implementation — see [author-outreach.md](author-outreach.md#scheduling)
for the schedule actually in use.** This section originally required the outreach run to
complete immediately before `send_weekly_summary`, in the same cron slot, because both read
the same candidate set and any gap lets new articles change the ranking between the promise
and the send.

That turned out to be unimplementable as stated: the queue needs a human to approve rows
between the build and the send, so the two cannot share a cron slot. The schedule builds
Thursday and sends Monday instead, and the drift the same-slot rule was protecting against is
handled at send time — `send_author_outreach` re-runs the article-level gates immediately
before dispatch and returns any row that has lost a paper to `pending` rather than sending a
promise that is no longer true.

If the digest then fails to run, the promise is delayed rather than broken: the article stays
in the candidate set and goes out the following week.

### Addressing and sending

| Item | Decision |
|:-----|:---------|
| Which address | `Authors.emails[0]`, lowercased. One address only — no fallback to the second on failure |
| From | `gregory@<site.domain>`, using the site's existing `sender_name` / `sender_email_prefix` |
| Reply-To | `bruno@brain-regeneration.com`, stored per campaign so other sites can differ |
| Tag | `author_outreach` |
| Body | Minimal HTML — paragraphs and `<a>` tags, no `base_email.html` wrapper — plus a real `.txt` alternative |
| UTM | `utm_medium=email`, `utm_source=author_outreach`, `utm_campaign=<campaign slug>`, `utm_content` in `article_link` / `author_page` / `site` |

### Configuration

A dedicated `AuthorOutreachCampaign` model per site, not booleans on `CustomSetting`. It has
to hold the mode, the subject selection, the copy override, the safety limits, and a halt flag
— `CustomSetting` is the wrong shape for that, and a campaign needs to be pausable and audited.

`mode` is `upcoming` (default) or `retrospective`. It selects between the two eligibility rule
sets above, and `featured_within_days` applies only in `retrospective` mode — validated in
`clean()` so an `upcoming` campaign cannot carry a window that does nothing.

The copy override is a plain `TextField` of Django template syntax, not CKEditor. Blank means
the packaged default template. Rendered against an explicit context of strings and dicts only
— never model instances — so an admin-authored template cannot walk ORM relations.

Placeholders: `{{ author_name }}`, `{{ articles }}` (list of `{title, url}`),
`{{ article_title }}` / `{{ article_url }}` (the first one), `{{ author_page_url }}`
(the tagged href) and `{{ author_page_url_display }}` (the untagged,
scheme-stripped anchor text),
`{{ site_name }}`, `{{ site_url }}`, `{{ sender_name }}`, `{{ opt_out_url }}`.

**A campaign cannot be enabled on a site whose `CustomSetting.has_author_pages` is False.**
Model-level `clean()` refuses it — the email's entire second half is about the author page.

### Queue and approval

Two phases, two commands, one human in between.

1. `build_author_outreach --campaign <slug>` evaluates eligibility and writes `AuthorOutreach`
   rows with `status="pending"`.
2. A human reviews the queue in Django admin and approves rows (`status="approved"`).
3. `send_author_outreach --campaign <slug>` sends **only approved rows**.

Nothing reaches a researcher without someone having looked at it. Given the measured volume
of 0–3 per week, review is a minute of work.

### The first run, and the back catalogue

The back-catalogue send is **a second campaign on the same site**, `mode="retrospective"`, not
a flag on the steady-state one. It gets its own `featured_within_days` (e.g. 90), its own
`utm_campaign_slug`, and its own `body_template` — the copy *must* differ, because the drafted
copy's "it will be featured in the next research digest" is future tense and false for a paper
featured months ago. Past tense there is not a nicety; it is the difference between a true
statement and a false one.

This needs no extra machinery. `AuthorOutreach`'s uniqueness is on `(site, author)`, not
`(campaign, author)`, so an author reached by the back-catalogue campaign is automatically
excluded from the steady-state campaign forever. The distinct campaign slugs also mean Umami
separates back-catalogue clicks from steady-state ones without any further work.

Two enabled campaigns on one site is legal; the slot goes to whichever builder runs first.
Disable the back-catalogue campaign once its queue is drained.

### Safety limits

Proposed numbers, all stored on the campaign so they can be tuned without a deploy:

| Guard | Threshold | Reasoning |
|:------|:----------|:----------|
| Spam complaints | Halt at **2** absolute, or **>0.1%** once ≥500 sent | Postmark treats ~0.1% as the acceptable ceiling; at this volume the absolute number binds first |
| Hard bounces | Halt at **10** absolute, or **>5%** once ≥40 sent | Postmark reviews accounts around 10%; 5% leaves margin |
| Postmark 406 (inactive recipient) | Halt at **5** | Means we are repeatedly targeting already-suppressed people — a query bug, not bad luck |
| Send rate | 20/minute | Trivially slow at this volume; exists so a future backfill cannot burst |
| Daily cap | 50 | Ditto |

Halting sets `campaign.halted=True` with a reason and requires a human to clear it. The guards
are evaluated before every individual send, not once per run.

### One row per site per author

`UniqueConstraint(site, author)` on `AuthorOutreach`. A **failed send burns the slot** for
that site — no automatic retry. A different site in the same database may still try the same
author, subject to its own campaign's rules. Re-opening a burned slot is a deliberate,
superuser-only admin action, logged.

### Event storage

Store events for **all** email the system sends, not just outreach. Two new tables:

- `EmailMessage` — one row per message handed to Postmark, by any sender (weekly digest, admin
  summary, trial notification, announcement, outreach). Holds the Postmark `MessageID`, an
  opaque `msg_token`, recipient, tag, stream, and the aggregate outcome fields the webhook
  updates.
- `EmailEvent` — one row per webhook event, appended.

Correlation uses **two independent keys**: the `MessageID` Postmark returns at send time, and
an opaque per-message UUID echoed back in `Metadata`:

```json
"Metadata": {"msg_token": "<uuid4>", "campaign": "<campaign-slug>"}
```

No author ID, subscriber ID, or any other resolvable identifier goes into `Metadata`. Nothing
that leaves the database can be traced back to a person by anyone holding only the Postmark
side.

### Privacy of tracking data

Open and Click payloads carry IP, GPS coordinates, city, region, and user agent for a named
researcher who never consented to being tracked. **None of it is stored.**

`EmailEvent` keeps: record type, timestamp, message correlation keys, recipient, tag, stream;
for Bounce also `Type`/`TypeCode`/`Details`; for Click the `OriginalLink` (our own URL). It
drops `Geo`, `IP`, `UserAgent`, `OS`, `Client`, `Platform`, `ReadSeconds`, and the whole raw
payload — Bounce payloads additionally embed the full message `Content`, which is never stored.

Open and link tracking are enabled **per message** for outreach only, so digest emails keep
their current behaviour. The opt-out link itself is excluded from click tracking.

### Bounce and complaint handling

| Event | Action |
|:------|:-------|
| Hard bounce / bad address | `AuthorContactOptOut(reason="hard_bounce")` — the address is never used again anywhere |
| Spam complaint | `AuthorContactOptOut(reason="spam_complaint")`, global, permanent; counts toward the circuit breaker |
| Subscription change (unsubscribe) | Existing `handle_subscription_change` path, plus an `AuthorContactOptOut` when the address matches an outreach recipient |
| Opt-out link | `AuthorContactOptOut(reason="opt_out")`, global |

`AuthorContactOptOut` is keyed on the email address, independent of `Subscribers`. An author
who is also a subscriber is protected by both.

## Copy

The draft copy is accurate as written for an `upcoming` campaign, and needs no change. Its two
claims both hold.

**"It will be featured in the next research digest newsletter."** True by construction:
eligibility rule 2 admits only articles that have never been sent for that list and that rank
within `article_limit` of the digest's own priority order, and `send_author_outreach` re-runs
those same gates immediately before dispatch, returning any row that has lost a paper to
`pending` instead of sending. A rollover can still push an article one edition later, which is
why the rule is never-sent *and* top-N rather than either alone.

**"A high score in these three models and the human review."** Every candidate article has
passed through a curator review surface: the admin summary emails ML-scored articles at the list's
`ml_threshold` to curators, and `select_digest_articles` excludes any article a curator marked
`is_relevant=False` before it can reach a digest — in both relevancy and date sort modes
(`filter_articles_excluding_all_irrelevant`, `subscription.py:100`). An article qualifying on
ML consensus alone was therefore reviewed and not rejected; a curator marking it relevant only
reinforces a decision the ML already made.

`retrospective` campaigns need their own past-tense body, and cannot use the packaged default.

One narrow exception, handled in the eligibility engine rather than the copy: the digest's
exclusion only fires when an article is marked not-relevant for **all** of its list-shared
subjects. A paper rejected for subject A but unreviewed for subject B is still featured. The
outreach builder therefore applies a stricter per-subject guard — see eligibility rule 3.

## Non-goals

- No per-recipient identifier in any URL. The opt-out token is the sole exception and resolves
  to a single `AuthorOutreach` row, not to a person's identity in an analytics tool.
- No follow-up, drip, or second email of any kind. One per author per site, forever.
- No change to `MessageStream` for existing senders — everything stays on broadcast, so
  `postmark_webhook.EXPECTED_MESSAGE_STREAM` needs no change.
- No API exposure. `Authors.emails` stays out of every serializer, and none of the new models
  gets an endpoint.

## Retention

Pruning is about purpose limitation, not disk — the whole event log is on the order of 30–50k
rows a year at current volumes. Every fact that must survive lives outside `EmailEvent`.

| Table | Retention | Reasoning |
|:------|:----------|:----------|
| `EmailEvent` | **180 days** | Operational telemetry: deliverability debugging, comparing one send against the previous. After two quarters nothing needs it. A row is an identifiable researcher's address plus the fact they opened a message — it should not sit around for a year unused. Also comfortably longer than Postmark's own ~45-day activity retention |
| `EmailMessage` | **24 months**, and **never** when referenced by an `AuthorOutreach` | Carries the per-message outcome flags, which are what a historical bounce-rate or complaint-rate check actually reads. For an outreach message it is the evidence that the one-time contact happened, so it outlives the window |
| `AuthorContactOptOut` | Indefinite | "Never contact this address" cannot expire |
| `SuppressionEvent` | Indefinite | Already the case today |
| `AuthorOutreach` | Indefinite | The slot is burned permanently, so the record has to outlive everything else |

**Invariant: pruning telemetry must never weaken a suppression.** Any future change to the
prune commands is measured against that sentence.
