# Author Outreach

## What this does

Author outreach sends a single, one-time email to the authors of a research
paper that a GregoryAI site is about to feature (or, for a one-off back
catalogue send, has already featured) in its weekly research digest
newsletter. The email tells the author their paper was selected, points
them at their author profile page, and gives them a real way to say "never
contact me again." One email per author per site, ever — there is no
follow-up, no drip, no second message of any kind.

This is cold outreach, signed with a real person's name, sent to a
researcher who never subscribed to anything. It runs on a completely
separate legal basis and delivery path from the newsletter: legitimate
interest rather than consent (see
[Legal basis: a legitimate interest balancing test](#legal-basis-a-legitimate-interest-balancing-test)
below), Postmark's broadcast stream, and a queue that a human has to
approve before anything sends.

### Forward-looking timing

The tense matters and drives the whole design: in the default (`upcoming`)
mode, the email goes out **ahead of** the digest, not after it. "Your paper
will be featured in the next research digest newsletter" has to be true at
send time, so eligibility is read from the digest's own selection function
— `subscriptions.management.commands.utils.subscription.select_digest_articles`
— never from a record of what the digest has already sent. This is the same
function `send_weekly_summary` and the staff email-preview harness both
call, so outreach can never promise an article the digest would not
actually pick.

A digest *candidate* is not the same as *will be featured*.
`select_digest_articles` works over the list's `lookback_days` (30 by
default), so its candidate set includes articles that were already sent
weeks ago — no subscriber will see those again. The eligibility engine
narrows the candidate set further before treating anything as promise-safe:
an article has to have **no** `SentArticleNotification` row for that list
yet, and it has to rank inside `list.article_limit` by the digest's own
priority order. Both conditions together are what keep the promise true;
either alone is not enough. Measured against the dev database, MS Weekly
Digest had 12 raw candidates but only 5 that had never been sent — the gap
between "candidate" and "will be featured" is not theoretical.

---

## Campaign modes and eligibility

An `AuthorOutreachCampaign` runs in one of two modes. Rules 3 through 8
below are shared between them; only the first step — which articles even
enter the pool — differs.

### Mode upcoming

The default, and the mode the packaged default email copy is written for.

1. The article is in the candidate set `select_digest_articles(list,
   list.lookback_days)` returns for a weekly digest list on the campaign's
   site.
2. The article has no `SentArticleNotification` row for that list (never
   sent to anyone yet), and it ranks within `list.article_limit` once the
   never-sent candidates are ranked by the list's own priority order
   (`rank_and_limit_articles` — the identical helper `send_weekly_summary`
   uses to truncate a subscriber's article set).

### Mode retrospective

Back catalogue only — a one-time send about a paper that was already
featured, used to reach researchers who joined before author outreach
existed. Never the steady-state mode.

1. A `SentArticleNotification` row exists for `(article, list)` on the
   campaign's site.
2. That send happened within `campaign.featured_within_days` of now (or,
   for a `--dry-run` preview only, the `--featured-since DAYS` override —
   see [Command reference](#command-reference)).

Zero of the 1,547 articles ever featured in a weekly digest, measured
against the dev database, had a `published_date` within 7 days of being
featured — discovery lag from PubMed and the source feeds runs weeks to
months. That is why `retrospective` eligibility keys off `sent_at`, never
`published_date`: a `published_date` window would send nothing, permanently.

A `retrospective` campaign needs its own past-tense `body_template` — the
packaged default's "will be featured in the next digest" is written for
`upcoming` mode and is false, in the past tense, for anything already
featured. A `retrospective` campaign left with a blank `body_template`
raises rather than silently falling back to that copy; `send_author_outreach`
checks this once, up front, before touching any row, so a whole campaign's
worth of sends can never burn slots on a configuration mistake one row at a
time. See [First-run runbook](#first-run-runbook) below.

### Shared eligibility rules

3. The article passes a relevance gate for at least one of that list's
   subjects: ML consensus (per `Subject.ml_consensus_type`, scored at
   `list.ml_threshold`, using the same shape `api.filters.ml_relevant_articles_q`
   builds) **or** a manual `ArticleSubjectRelevance.is_relevant=True` —
   union, not intersection. A subject the article is explicitly marked
   `is_relevant=False` for cannot satisfy this rule through that subject,
   even when ML consensus passes for it.
4. If the campaign names subjects (`AuthorOutreachCampaign.subjects` is
   non-empty), only weekly digest lists sharing at least one of those
   subjects are processed. An empty subject set means every weekly digest
   list on the site — which is how one campaign can cover an entire site
   without a campaign per list.
5. The author has a non-empty `Authors.emails`, a set `Authors.ORCID`,
   `orcid_verified_email=True`, and `orcid_claimed=True`.
6. The address is not in `AuthorContactOptOut`, does not belong to a
   `Subscribers` row with `active=False`, and has no `SuppressionEvent`
   whose most recent row has `suppress_sending=True`.
7. No `AuthorOutreach` row already exists for `(site, author)`, in any
   status, from any campaign.
8. Every author on a qualifying paper is eligible — ORCID gives no reliable
   authorship position, so first/last/corresponding author cannot be
   distinguished from the data GregoryAI has.

Up to `campaign.max_articles_per_email` qualifying papers are named in one
email (default 3), most recent `published_date` first. One email per
author, regardless of how many qualifying papers they have.

### Where outreach is stricter than the digest

Rule 3 is deliberately stricter than the digest's own
`filter_articles_excluding_all_irrelevant`
(`subscriptions/management/commands/utils/subscription.py`), which only
drops an article rejected across **every** one of its list-shared subjects.
A paper a curator rejected for subject A but never reviewed for subject B
still reaches the digest through B. Outreach cannot make the same call: an
article rejected for the campaign's subject must not trigger an email whose
second claim is curator approval for that same subject, even though the
digest would still feature the article via an unrelated subject. This is
the one place outreach and the digest intentionally disagree, and it is
covered by a named regression test
(`test_explicit_irrelevant_on_one_subject_blocks_despite_digest_featuring_it`)
rather than left as an aside in a comment.

A second, narrower divergence: `upcoming` mode additionally requires that
`build_author_outreach` only ever writes a real (non-dry-run) queue while
`campaign.enabled` is `True` — `--dry-run` is allowed on a disabled
campaign, so a campaign can be previewed while it is still being
configured, but nothing reaches the approval queue from it until someone
switches it on.

---

## Legal basis: a legitimate interest balancing test

Every `AuthorOutreach` row records `legal_basis="legitimate_interest"`
(GDPR Art. 6(1)(f)), with a `basis_note` naming the campaign and build
command that queued it. This section is the balancing test that basis
rests on — written to be shown, as written, to a regulator or to a
researcher who asks why they were emailed.

### Purpose test

The legitimate interest pursued is connecting a researcher with a platform
that has already indexed and is about to publicly feature their own
published work, so they can see, correct, and enrich the record GregoryAI
holds of their research — their author profile page — and so the
scientific community following that subject (multiple sclerosis and
related neuroimmune research, at the time of writing) benefits from a more
complete and more accurate index. This is a real, specific, present
interest, not a generic marketing objective: the trigger for every email is
a concrete paper the recipient wrote, not a purchased list or a scraped
directory.

### Necessity test

There is no less intrusive way to reach this specific researcher about this
specific paper. GregoryAI has no prior relationship with them — they are
not a subscriber, and nothing about being indexed as an author implies
consent to be contacted. The only channel available is the public,
scholarly-contact email address the researcher themselves published on
their own ORCID record, and outreach uses only that address
(`Authors.emails[0]`), never a second guess, a scraped address, or an
address sourced from anywhere else. Processing is limited strictly to what
the purpose requires: one email, ever, naming only the paper(s) that
triggered it and a link to the profile page — no profiling, no behavioural
targeting, no sale or transfer of the address to a third party, no
automated decision with any legal or similarly significant effect on the
recipient.

### Balancing test

Weighed against the researcher's own rights and reasonable expectations:

- An ORCID record's public email field exists specifically so a
  researcher's own published work can reach them about that work — this
  contact is squarely inside what a researcher who published that address
  would reasonably expect, not outside it.
- The email is about the recipient's own paper, sent once, with a real and
  working opt-out. It carries no third-party advertising, no unrelated
  content, and no request for money, data, or action beyond an optional
  visit to a page about their own research.
- Volume is small by measurement, not by aspiration: 2 to 3 eligible
  authors for the next `upcoming` send across every weekly digest list on
  the reference site, and 17 for a 90-day `retrospective` back catalogue
  window, against a database of nearly 258,000 authors overall. This is not
  bulk marketing at scale; it is a handful of individually-reviewed emails
  a week.
- The interest is not overridden by any special category of data — the
  processing touches a professional contact address and a publication
  record, not health, biometric, or similarly sensitive personal data about
  the researcher themselves.

The balance tips in favour of processing, subject to the safeguards below —
remove any one of them and the balance would need to be re-struck.

### Safeguards

- **One-time contact, enforced by the schema.** `unique_author_outreach_per_site`
  makes `(site, author)` a slot claimable exactly once, ever, across every
  campaign on that site — not a policy a sender has to remember to honour,
  a constraint the database enforces.
- **Source address is the researcher's own public, verified ORCID email.**
  Eligibility requires `orcid_verified_email=True` and `orcid_claimed=True`
  — the record has to be one the researcher themselves claimed and
  verified, not an unclaimed ORCID entry populated from elsewhere.
- **A real opt-out**, not a formality. `AuthorOutreach.opt_out_token`
  resolves to a working `POST` endpoint
  (`/subscriptions/author-optout/<token>/`) that writes a permanent,
  global `AuthorContactOptOut` row — see
  [Approval workflow](#approval-workflow) and
  [subscriptions.md](subscriptions.md#author-outreach-opt-out) for the full
  mechanics. The opt-out affects future email only; it never hides, alters,
  or unpublishes the author's profile page — declining contact is not the
  same as declining to be indexed. The email enables Postmark click
  tracking on every other link (see below), so the opt-out anchor itself
  carries Postmark's `data-pm-no-track` attribute — verified against
  Postmark's own developer documentation, which names this exact attribute
  for excluding one link from tracking while the rest of the message stays
  tracked — so clicking "never contact me again" is not itself logged as a
  tracked click.
- **No tracking data retained.** Open and Click webhook payloads carry IP
  address, GPS coordinates, city, region, and user agent for the named
  researcher. None of it is stored anywhere — see
  [subscriptions.md § Email Message and Event Log](subscriptions.md#email-message-and-event-log)
  for the exact field list `EmailEvent` keeps and the regression test that
  asserts the rest is dropped, not merely unused.
- **Human approval before every send.** `build_author_outreach` writes
  `pending` rows and sends nothing. `send_author_outreach` sends only
  `approved` rows. No email reaches a researcher without a person having
  looked at the specific row first — see
  [Approval workflow](#approval-workflow).
- **Broadcast stream, not transactional.** A complaint on this feature's
  mail cannot degrade deliverability for any transactional message the
  system sends, because there is no shared stream between them.
- **Circuit breakers** halt an entire campaign automatically on early signs
  the balance may not hold for a specific send pattern — see
  [Circuit breakers](#circuit-breakers).
- **Hard bounce or spam complaint on *any* mail this system sends** — not
  only outreach — permanently opts the address out of outreach, via the
  same `AuthorContactOptOut` table, independent of which sender triggered
  it.

---

## Approval workflow

Two management commands, two phases, exactly one human step in between.

1. `build_author_outreach --campaign <slug>` evaluates every eligibility
   rule above against the live database and writes `AuthorOutreach` rows
   with `status="pending"`. It never sends anything and never touches an
   existing row.
2. A human reviews the queue in Django admin
   (Subscriptions → Author Outreach) and runs the **Approve selected**
   action on the rows that should go out, or **Skip selected** on rows
   that should not. Given the measured volume — 0 to 3 rows a week in
   `upcoming` mode — this is a minute of work, not a bottleneck.
3. `send_author_outreach --campaign <slug>` sends **only** rows with
   `status="approved"`. A `pending` row is never sent, regardless of
   `--limit` or how the queue looks.

Immediately before every individual send — not once per run —
`send_author_outreach` re-checks the recipient's address against the
opt-out/suppression tables (the same checks `build_author_outreach` applied
at queue-build time) and re-evaluates the campaign's circuit breakers
against its live `EmailMessage` aggregates. Either can stop a row that
looked fine when the queue was built, or even one row earlier in the same
run.

### Admin actions

| Action | Who | Effect |
|:-------|:----|:-------|
| Approve selected | Any staff with change permission | Moves `pending` rows to `approved`, stamping `approved_at`/`approved_by`. Rows not currently `pending` are reported and left alone. |
| Skip selected | Any staff with change permission | Moves `pending`/`approved` rows to `skipped`. Burns the slot — see below. |
| Reset for retry | Superuser only | Reopens a slot the rules had closed. See below. |

Every field on an `AuthorOutreach` row is read-only in the admin outside
these actions, and the row cannot be deleted (bulk or individual) — the
retention table below marks this row "indefinite," so a silent field edit
or a delete would erase the one thing that has to survive.

A failed, skipped, or cancelled row burns its `(site, author)` slot
permanently — there is no automatic retry, by design (see
[Legal basis](#legal-basis-a-legitimate-interest-balancing-test): one-time
contact is a safeguard, not an inconvenience to route around). **Reset for
retry** is the sole, deliberate, logged exception, restricted to
superusers. It also covers a row stuck in `status="sending"` — the
crash-safety marker `send_author_outreach` sets immediately before calling
Postmark — but only when **no** `EmailMessage` row exists yet for that
recipient/site/tag. A matching `EmailMessage` is positive evidence the
message may already have reached Postmark, so that row is refused instead
of reset: resetting it would risk a genuine second send to the same
person, which the one-email-per-author-per-site rule forbids outright. A
refused row needs a human to read `EmailMessage.accepted` and its
`EmailEvent` history before deciding anything further.

---

## Circuit breakers

Every threshold below is a field on the campaign (`AuthorOutreachCampaign`),
tunable per campaign without a deploy. `send_author_outreach` evaluates all
of them, scoped to that campaign's own `EmailMessage` rows only, before
every individual send.

| Guard | Default threshold | Field |
|:------|:-------------------|:------|
| Spam complaints, absolute | 2 | `complaint_halt_absolute` |
| Spam complaint rate | greater than 0.1%, once at least 500 sent | `complaint_halt_rate_percent` / `complaint_halt_rate_min_sent` |
| Hard bounces, absolute | 10 | `bounce_halt_absolute` |
| Hard bounce rate | greater than 5.0%, once at least 40 sent | `bounce_halt_rate_percent` / `bounce_halt_rate_min_sent` |
| Postmark 406 (inactive recipient), absolute | 5 | `inactive_halt_absolute` |
| Send rate | 20 per minute | `send_rate_per_minute` |
| Daily cap | 50 per day | `daily_send_limit` |

The absolute complaint and hard-bounce thresholds bind first at this
feature's measured volume — the rate thresholds exist for when the feature
is used at larger scale later. A repeated 406 means the query is
repeatedly targeting people Postmark already considers suppressed: a query
bug, not bad luck, which is why its threshold is low and absolute-only.

The moment any threshold is met or exceeded, the run sets
`campaign.halted=True` with a human-readable `halted_reason`, and stops
immediately — the row being considered when the breaker tripped is left
untouched (still `approved`, never sent), and every row after it in that
run is never reached. A campaign that is already halted refuses to send
anything at all, from the very start of the run.

### Clearing a halt

A halt does not clear itself. In the Django admin, open the campaign,
read `halted_reason`, investigate the underlying `EmailMessage`/`EmailEvent`
rows for what actually happened, then uncheck **Halted** and save. There is
no "resume" action separate from this — clearing the flag and re-running
`send_author_outreach` is the resume.

---

## Retention

| Table | Retention | Command |
|:------|:----------|:--------|
| `EmailEvent` | 180 days | `prune_email_events --days N --dry-run` |
| `EmailMessage` | 730 days, except any row an `AuthorOutreach` references | `prune_email_messages --days N --dry-run` |
| `AuthorContactOptOut` | Indefinite | Never pruned |
| `SuppressionEvent` | Indefinite | Never pruned |
| `AuthorOutreach` | Indefinite | Never pruned; deletion disabled in the admin |

`EmailMessage`'s exclusion is a real, unconditional query against
`AuthorOutreach.email_message`, not an approximation — a message a
one-time contact actually used is the evidence that contact happened, and
it has to outlive the 730-day default for that reason alone.

**Invariant: pruning telemetry must never weaken a suppression.** Both
prune commands state this in their own docstring, and neither one ever
touches `AuthorContactOptOut` or `SuppressionEvent`. Any future change to
either command is measured against this sentence before anything else.

---

## First-run runbook

The very first author-outreach send on a site should be the back-catalogue
`retrospective` campaign, not the steady-state `upcoming` one — it reaches
researchers whose papers were already featured before this feature existed,
and its small, fixed queue makes it a safe place to verify the whole
pipeline end to end before `upcoming` mode starts running on a schedule.

1. In Django admin, create an `AuthorOutreachCampaign` with `mode`
   `retrospective`, its own `utm_campaign_slug` (distinct from the
   steady-state campaign's, so Umami can tell the two apart), a
   `featured_within_days` window sized to the back catalogue being reached
   (90 days reached 17 authors against the dev database), and its own
   past-tense `body_template` — the packaged default's "will be featured in
   the next digest" is written for `upcoming` mode and is false, in the
   past tense, for anything already featured. A `retrospective` campaign
   left with a blank `body_template` refuses to render rather than
   silently falling back to that copy.
2. Run `build_author_outreach --campaign <slug> --dry-run` first and read
   the printed list of candidates. This never writes a row, so it is safe
   to run repeatedly while tuning the campaign's subjects or window.
3. Enable the campaign, then run `build_author_outreach --campaign <slug>`
   for real. Review the queue in the admin — every candidate the dry run
   showed should now be a `pending` `AuthorOutreach` row.
4. Approve exactly **one** row.
5. Run `send_author_outreach --campaign <slug> --limit 1`.
6. Watch for the Delivery webhook on that message before approving or
   sending any more — confirm the `EmailMessage` row's `delivered_at` gets
   set, and that no unexpected `Bounce` or `SpamComplaint` event shows up
   for it. This is the first real signal that the Postmark side (stream,
   tracking flags, webhook wiring) is configured correctly, not just that
   the code ran without an exception.
7. Once satisfied, approve and send the rest of the queue at whatever pace
   feels comfortable — `--limit` bounds how many go out per invocation, and
   the campaign's own `daily_send_limit`/`send_rate_per_minute` bound the
   rest.
8. Disable the campaign once its queue is drained. It is a one-time send;
   leaving it enabled only means the next `build_author_outreach` run
   re-evaluates a `featured_within_days` window that has no reason to
   produce new candidates (an author who qualified once is already
   excluded from ever qualifying again, on this site, by
   `unique_author_outreach_per_site` — but there is no reason to keep
   spending a cron slot confirming that).

Only after this has run cleanly once should the steady-state `upcoming`
campaign be enabled and put on the cron schedule below.

---

## Cron ordering

Cron ordering is load-bearing, not a convenience. In `upcoming` mode, the
build and the send have to run in the same slot, immediately **before**
`send_weekly_summary` — both `build_author_outreach` and
`send_weekly_summary` read the same candidate set
(`select_digest_articles`), and any gap between them lets a new article
arrive and change the ranking, breaking the "will be featured in the next
digest" promise the email already made. `send_weekly_summary` takes no
`--list` argument — one invocation covers every weekly digest list on
every site — so one campaign per site, with an empty `subjects` set
(covering every weekly digest list on that site), needs exactly one
build-and-send line ahead of the single shared `send_weekly_summary` line.
A site running more than one `upcoming` campaign (scoped to different
`subjects`) needs one build-and-send line per campaign, all still ahead of
that same shared line.

```cron
# Author outreach (upcoming) for gregory-ms.com — must finish before
# send_weekly_summary below, since both read select_digest_articles's
# candidate set and a gap between them changes the ranking. One line per
# site running an enabled upcoming campaign; repeat with that site's own
# --campaign slug for additional sites.
50 7 * * 2 flock -n /tmp/author_outreach_ms-weekly-outreach docker exec gregory sh -c "python manage.py build_author_outreach --campaign ms-weekly-outreach && python manage.py send_author_outreach --campaign ms-weekly-outreach"

# Weekly summary every Tuesday — unchanged
5 8 * * 2 docker exec gregory python manage.py send_weekly_summary
```

The `flock` guard (matching the pipeline entry in `CLAUDE.md`) stops a slow
run from overlapping with itself on the next cron tick; it says nothing
about the fifteen-minute gap before `send_weekly_summary`, which exists
only to leave the send command comfortable headroom under
`send_rate_per_minute`/`daily_send_limit` at this feature's measured
volume. If the digest fails to run after outreach has sent, the promise is
delayed rather than broken — the article stays a candidate and goes out
the following week. `retrospective` campaigns are one-off and are never
part of this recurring schedule; run them by hand, per the
[first-run runbook](#first-run-runbook) above.

---

## Postmark setup

The webhook endpoint and its credentials already exist and do not change:
`POST /webhooks/`, HTTP basic auth via `POSTMARK_WEBHOOK_USERNAME` /
`POSTMARK_WEBHOOK_PASSWORD`, configured on the broadcast message stream —
see [subscriptions.md § Suppression and Reactivation Webhook](subscriptions.md#suppression-and-reactivation-webhook)
for the full contract. Author outreach only changes which event types that
same webhook is subscribed to.

In the Postmark UI, on the broadcast stream's existing webhook
configuration, add:

- Delivery
- Bounce
- Spam Complaint
- Open (first open only — matches how `EmailMessage.first_opened_at` and
  the `Open` `EmailEvent` type are already modelled; do not enable every
  open)
- Link Click

Delivery, Bounce, and Open are typically already enabled for the digest
sends; Spam Complaint and Link Click are the two genuinely new additions
this feature needs, since digest mail never opts into click tracking.

Open and link tracking (`TrackOpens`/`TrackLinks`) are not a stream-level
setting — they are sent per message by `send_author_outreach` only, so
enabling the event types above changes nothing about what Postmark reports
for the weekly digest, admin summary, trial notification, or announcement
sends, which never set those flags.

Before the first real send, confirm the campaign's `reply_to` address is a
deliverable inbox — a bounce on the reply address itself would not be
caught by any of this feature's own suppression handling.

---

## Command reference

```text
build_author_outreach --campaign <slug> [--featured-since DAYS] [--dry-run] [--limit N]
```

- `--campaign` — required. `AuthorOutreachCampaign.utm_campaign_slug`.
- `--featured-since DAYS` — `retrospective` campaigns only, and requires
  `--dry-run`. Overrides `campaign.featured_within_days` for one preview
  run without changing the stored campaign field, so the rules behind any
  real send always stay recoverable from the campaign record rather than
  from shell history.
- `--dry-run` — evaluates and prints eligibility; writes nothing. Allowed
  even when `campaign.enabled` is `False`.
- `--limit N` — stop after this many authors.

```text
send_author_outreach --campaign <slug> [--limit N] [--dry-run] [--test-to ADDRESS]
```

- `--campaign` — required.
- `--limit N` — send at most this many rows this run.
- `--dry-run` — renders and lists what would be sent; calls Postmark for
  nothing, writes no `EmailMessage`, changes no `AuthorOutreach` row.
- `--test-to ADDRESS` — renders one real row's content and sends it to
  `ADDRESS` instead of that row's own recipient. Leaves the row's `status`,
  `sent_at`, and `email_message` completely untouched — for verifying
  rendering, not for advancing the queue.

---

## Related docs

- [subscriptions.md](subscriptions.md) — the subscriber/list system this
  feature sits alongside, including the full `EmailMessage`/`EmailEvent`
  contract, the suppression/reactivation webhook, and the author opt-out
  endpoint's wiring.
- [02.1-database-tables-and-fields.md](02.1-database-tables-and-fields.md)
  — field-by-field reference for `EmailMessage`, `EmailEvent`,
  `AuthorOutreachCampaign`, `AuthorOutreach`, and `AuthorContactOptOut`.
