# Plan: Postmark webhook for suppression and reactivation

Task 8 of [subscriptions-remaining-work.md](subscriptions-remaining-work.md).
Replaces the current reactive-only suppression handling, which learns about a
bounce by attempting a send and failing.

This is the largest remaining item and the only one adding new public surface
area. Configuration and reactivation policy are both settled — there are no open
decisions. Start with the `SuppressionEvent` model; nothing about reactivation
works until suppression records what it changed.

## Preconditions

- Branch off `main` before touching anything — CI deploys everything merged there.
- Tests from `django/`: `pytest subscriptions`. Baseline is 2,836 across the full suite.
- Lint and format on the host: `uvx ruff check django/` and `uvx ruff format django/`.

---

## Confirmed configuration

Both pre-flight questions are settled (Bruno, 2026-07-29):

- the webhook is configured on the **broadcast** stream, which is what `send_email` sends on
- it posts to `https://api.brain-regeneration.com/webhooks/`
- there are no transactional emails yet, so broadcast is the only stream in use — `MessageStream` becomes a sanity check to record and assert, not a routing decision

Routing: the project has a single `ROOT_URLCONF` (`admin.urls`) serving every
domain, so `path("webhooks/", ...)` there is reachable at that URL with no
per-domain configuration.

Two things to verify while wiring it up:

- the api vhost must pass the `Authorization` header through to Django. Nginx normally does, but if it is stripped, basic auth silently never arrives and every request looks unauthenticated.
- the path is `/webhooks/`, not `/webhooks/postmark/`. It is provider-agnostic, so if anything else ever posts webhooks here it will collide. Dispatch on `RecordType` and reject payloads that do not look like Postmark's.

### Authentication: basic auth, and no signature

Postmark **does not support HMAC webhook signatures**. Their documentation says
so explicitly, while also carrying a TypeScript sample that calls
`verifyPostmarkWebhook(rawBody, signature)` against an `x-postmark-signature`
header. That sample is wrong — there is no such header to verify. Do not
implement signature checking.

The supported protections are HTTP basic auth, with credentials embedded in the
webhook URL Postmark posts to:

```
https://<username>:<password>@api.brain-regeneration.com/webhooks/
```

and optionally allowlisting Postmark's published webhook IP ranges at the
firewall. The origin IP changes per attempt, so the allowlist is a range, not a
host.

Put the credential in the environment, never in the repo. The endpoint changes
subscription state — an unauthenticated one lets anyone unsubscribe arbitrary
addresses.

**Reject with 403, not 401.** Postmark stops retrying on 403 and retries on
everything else. A wrong credential should fail once and loudly, not generate
hours of retries against an endpoint that will never accept them.

---

## What Postmark is configured to send

Enabled: Delivery, Bounce, Open (first open only), Subscription Change.
Not enabled: Spam Complaint, Link Click.

**Build on Subscription Change.** It is the superset event for suppression
state, and it fires in both directions:

| Field | Use |
|:------|:----|
| `Recipient` | the email address — maps to `Subscribers.email` |
| `SuppressSending` | `true` = suppress, `false` = the reactivation signal |
| `SuppressionReason` | `HardBounce`, `SpamComplaint`, or `ManualSuppression` |
| `Origin` | who initiated it — `Recipient` or `Customer` |
| `ChangedAt` | event time, needed for ordering |
| `MessageStream` | must match what we send on — see check 1 |
| `MessageID` | for idempotency |

Note that spam complaints still reach us through `SuppressionReason:
"SpamComplaint"` even though the Spam Complaint event type is disabled. Enabling
it would add detail, not coverage — it is optional.

Bounce events are useful supplementary detail (hard versus soft; a soft bounce
does not suppress). Delivery and Open are irrelevant to suppression. The
endpoint must still accept and ignore them with a 200, or Postmark will record
delivery failures and retry.

---

## The blocker: reactivation has nothing to restore

`subscriptions/utils/suppression.deactivate_subscribers` deactivates **every**
list subscription a person holds, in one bulk `queryset.update()`. It records
nothing about what it changed.

Simple-history does not help. `ListSubscription` declares
`history = HistoricalRecords()`, but simple-history hooks `save()` and
`post_save`, and every deactivation path in this app uses `queryset.update()` —
both `deactivate_subscribers` and `views._unsubscribe_confirm`. Measured on the
current data: **169 deactivated `ListSubscription` rows, 31 historical rows
recording a deactivation.** Roughly 82% of deactivations left no reconstructable
trace.

So when an unsuppress event arrives, there is no way to know which subscriptions
the suppression turned off versus which the person had already opted out of
themselves. Restoring everything would re-subscribe people to lists they left
deliberately.

**This must be fixed before reactivation can work at all.** Record what each
suppression changed, at suppression time:

- add a model — `SuppressionEvent` or similar — holding the subscriber, the Postmark `MessageID`, `ChangedAt`, reason, origin, stream, raw payload, and **the list of `ListSubscription` IDs it deactivated**
- have `deactivate_subscribers` write it, and return it
- reactivation then restores exactly that set, and nothing else

The same record doubles as the audit trail for the endpoint, and gives the
admin somewhere to show suppression state.

---

## Reactivation policy — decided

Settled by Bruno, 2026-07-30.

| `SuppressionReason` | On `SuppressSending: false` |
|:--------------------|:----------------------------|
| `HardBounce` | auto-restore |
| `ManualSuppression` | auto-restore |
| `SpamComplaint` | **never** — sticky, see below |
| anything else, or fields missing | record only, flag for review |

In every auto-restore case the 12-month staleness cap applies as well — see
below.

**Restore means both** the `Subscribers.active` flag and the exact
`ListSubscription` rows the suppression deactivated. Restoring only the global
flag is a no-op in practice — the subscriber holds no active subscriptions, so
they still receive nothing. This is why the `SuppressionEvent` record described
above is a prerequisite and not a nicety.

**Spam complaints are sticky.** A complaint is a recorded objection to
processing, and it outranks a later unsuppress — including one performed by your
own team in the Postmark UI. Never auto-restore. A human override in Django
admin may remain possible, but it must be an explicit, recorded action by a
named user, not a side effect of an incoming webhook.

### Fail safe on unexpected field combinations

The semantics of `Origin` on an *unsuppress* event are not established by the
documentation we have. On the suppress sample it reads as "what caused the
suppression" — `HardBounce` paired with `Origin: Recipient`, i.e. their mail
server. If that holds, `ManualSuppression` implies `Origin: Customer` and the
rule above needs no `Origin` qualifier at all. But on an unsuppress, `Origin`
may instead mean "who lifted it", and `SuppressionReason` may be absent
entirely.

Do not guess. Auto-restore only on a positive match against a known-good
combination; treat anything unrecognised — missing reason, unexpected origin,
new enum value — as record-only and surface it. Capture a real unsuppress
payload before relying on either field, and write down what it actually
contains.

### Staleness cap — decided

Consent decays. An unsuppress arriving long after the suppression would restore
subscriptions nobody has confirmed in a long time.

Confirmed by Bruno, 2026-07-30: **auto-restore only within 12 months** of the
original suppression. Beyond that, record and flag for review rather than
restoring.

So the full auto-restore condition is: reason is `HardBounce` or
`ManualSuppression`, **and** the original suppression is less than 12 months
old. Anything else is record-only.

Implementation notes:

- the cap is measured from the original suppression, not from the unsuppress event, so it depends on `SuppressionEvent` having recorded that timestamp. Another reason that model comes first.
- put the window in a named module constant (e.g. `REACTIVATION_MAX_AGE = timedelta(days=365)`) rather than a Django setting. It is a policy constant, not per-deployment configuration, and a settings key invites it being changed without the reasoning being revisited.
- **when no matching `SuppressionEvent` exists, fail safe and do not restore.** This is not a hypothetical: every suppression predating the model has no record — the three from 2026-07-28 and the handful already suppressed before that. Their age is unknowable, so they fall outside the cap by default and get flagged for a human.

That last point means reactivation will do nothing at all for existing
suppressions. That is the correct outcome — restoring a subscription whose age
and prior state are both unknown is exactly what the cap exists to prevent — but
say so in the admin surface so it does not read as a bug.

---

## The endpoint

Put it in `subscriptions/` — it changes subscription state, and
`subscriptions/views.py` already has the `csrf_exempt` import and pattern.

- POST only, `csrf_exempt`, authenticated per check 2
- Respond 200 quickly. Postmark retries on non-2xx, and slow responses cause duplicate deliveries. Do the work inline only while it stays fast; if it grows, record the payload and process out of band.
- Parse defensively: an unrecognised `RecordType` is a 200 and a log line, not an error.

### Retry behaviour, and why it constrains the design

Postmark retries any non-200, and gives up on 403. The schedules differ sharply
by event type:

| Events | Retry schedule |
|:-------|:---------------|
| Bounce, Inbound | 1min, 5min, 10min ×3, 15min, 30min, 1hr, 2hr, 6hr — ~10 hours |
| Delivery, Open, Click, **Subscription Change** | 1min, 5min, 15min — **~21 minutes** |

Subscription Change is the event this whole feature depends on, and it is in the
short bucket. More than about 20 minutes of downtime and those events are gone
permanently — Postmark will not replay them later.

Two consequences:

- respond 200 as fast as possible and do the work after acknowledging, rather than processing inline and risking a timeout that costs the event
- the reactive 406 handling **must stay**. It is the only thing that catches a suppression whose webhook was lost, and losing one is a normal operational event here, not a rare failure.

### Idempotency, and why not on `MessageID` alone

Postmark's own guidance is to dedupe on `MessageID`. That is wrong for this
event type. The Subscription Change sample payload carries
`"MessageID": "00000000-0000-0000-0000-000000000000"` — an all-zero placeholder,
because a suppression change is not always tied to a specific message. A manual
suppression in the Postmark UI has no originating message at all.

Deduping on `MessageID` alone would collapse every such event into one and
silently drop all but the first.

Use `(RecordType, Recipient, ChangedAt)` as the uniqueness key, and record
`MessageID` as data rather than identity. A duplicate is a 200, not an error.
Confirm the all-zero behaviour against a real payload once the endpoint is
live — if `MessageID` turns out to be populated and unique in practice, it can
be added to the key, but do not assume it.

### Ordering

Events can arrive out of order; a suppress and a later unsuppress could land
reversed, leaving someone suppressed who should not be. Compare `ChangedAt`
against the most recent applied event for that recipient and ignore anything
older.

### Unknown recipients

`Recipient` may not match any `Subscribers` row — a test send, an old address, a
different system on the same server. Record the event, do not error, do not
create a subscriber.

---

## Tags

Postmark allows **one** tag per message, set with the `Tag` field on the API
payload (or the `X-PM-Tag` SMTP header). `send_email` currently sends no tag.

Tags are not required for suppression — `Recipient` already identifies the
subscriber — but they make Postmark-side statistics and debugging much better,
and the webhook echoes the tag back.

Recommendation: tag by email type, which is low cardinality and matches how the
sends are already structured: `weekly_summary`, `admin_summary`,
`trial_notification`, `announcement`. Pass it through `send_email` as a
parameter with a sensible default. Do not tag by list id — Postmark's tag
reporting is designed for a small set, and list attribution is better derived
from our own records.

---

## Tests

- a valid Subscription Change with `SuppressSending: true` deactivates the subscriber and writes a suppression record naming the affected `ListSubscription` IDs
- `SpamComplaint` never auto-restores, including when the unsuppress originates from `Customer`
- `HardBounce` and `ManualSuppression` do auto-restore, and restore both the `active` flag and the recorded `ListSubscription` set
- an unsuppress with a missing or unrecognised `SuppressionReason` is recorded and flagged, not acted on
- a `HardBounce` unsuppress whose original suppression is 13 months old is recorded and flagged, not restored — the staleness cap
- an unsuppress for a subscriber with no `SuppressionEvent` record at all is recorded and flagged, not restored
- a replayed event is a no-op returning 200
- **two distinct suppressions both carrying the all-zero `MessageID` are both recorded** — the regression against deduping on `MessageID` alone
- an out-of-order event older than the last applied one for that recipient is ignored
- an unknown `Recipient` is recorded without error and creates no subscriber
- `Delivery`, `Open` and `Bounce` payloads return 200 and change no subscription state
- an unauthenticated POST is rejected **with 403**, so Postmark stops retrying
- a `MessageStream` other than `broadcast` is recorded but not acted on
- reactivation restores exactly the subscriptions the suppression deactivated, and not ones the subscriber had already opted out of — the regression for the blocker above

Postmark suggests testing with `curl` against the endpoint, or pointing the
webhook at RequestBin first to capture real payload shapes. Capturing one real
Subscription Change before finalising the dedup key is worth the detour, given
the `MessageID` uncertainty above.

---

## Docs

- `docs/subscriptions.md` — the webhook, the events consumed, the reactivation policy, and the suppression record
- `docs/02.1-database-tables-and-fields.md` — the new model
- `docs/01-install.md` or the deployment runbook — the webhook URL and its credential as a required setup step, since a fresh install would otherwise silently lack suppression handling

---

## Out of scope

- Delivery and Open analytics. The endpoint accepts and ignores them; building on them is separate work.
- Enabling the Spam Complaint or Link Click event types.
- Retiring the existing reactive 406 handling. It stays as a backstop, and not merely for misconfiguration: Subscription Change events are retried only three times over ~21 minutes, so a short outage loses them for good. The reactive path is what catches those. Both must converge on the same `deactivate_subscribers` helper so they cannot drift.
- Signature verification. Postmark does not offer it, whatever their sample code implies.
