# Data protection record: site-scope unsubscribe requests were not honoured

Record of a defect in the newsletter unsubscribe flow, kept for audit purposes.
Decision taken: fix forward, no proactive contact, document the incident.

Status: closed 2026-07-29. Fix deployed 2026-07-28; all investigation items
resolved. Affected individuals could not be identified — see below for why.

| Field | Value |
|:------|:------|
| Recorded | 2026-07-28 |
| Recorded by | Bruno Amaral |
| Defect introduced | 2026-04-16, commit `062cd221` |
| Detected | 2026-07-28, during a code audit of the subscriptions app |
| Exposure window | 2026-04-16 to date of fix (approximately 103 days as recorded) |
| Systems | GregoryAI subscriptions app, newsletter email footer |
| Remediation plan | [subscriptions-p0-fix-plan.md](../subscriptions-p0-fix-plan.md), Task A |
| Audit source | [subscriptions-audit-2026-07.md](../subscriptions-audit-2026-07.md), P0 finding 2 |

---

## What happened

Newsletter emails carry three unsubscribe links in the footer. One of them,
"Unsubscribe from all lists on <site>", never worked.

A recipient who clicked it was shown a confirmation page, submitted it, and was
then shown the standard "you have been unsubscribed" page. No subscription was
actually deactivated. The subscriber remained on every list and continued to
receive email.

The two other unsubscribe links in the same footer — "Unsubscribe from this
list" and "Unsubscribe from everything" — were unaffected and worked correctly
throughout.

## Technical cause

The footer link is built from the site the list sends from, which is stored on
`Lists.site` (`django/templates/emails/components/footer.html`). The view that
processes the request filtered on `list__team__site_id`
(`django/subscriptions/views.py`) — a different and nullable foreign key on the
`Team` model.

In the current data every list has `Lists.site = 3` while the corresponding
`Team.site` is either `1` or `NULL`, so the filter matched zero rows on every
request. Django's `QuerySet.update()` returns `0` in that case without raising,
and the view rendered its success page unconditionally.

The defect was introduced when the unsubscribe feature was first built
(2026-04-16); it was never a regression from working behaviour.

## Scope

Every newsletter email sent in the exposure window contained the non-functional
link, so all recipients in that period were exposed to it. The number who acted
on it is not known.

Figures below are from the **production** database on 2026-07-29, via
`scripts/incident-2026-07-28-scope-check.sh`.

| Measure | Value |
|:--------|:------|
| Total subscriber records | 540 |
| Subscribers holding at least one active subscription | 194 |
| Distinct subscribers with a retained send record since 2026-06-29 | 159 |
| Unsubscribe requests successfully recorded since 2026-04-16 | 144 |
| Subscribers known to have used the site-scope link | not recorded |

The production data confirms the defect's shape: all nine lists have
`Lists.site = 3`, while `Team.site` is `1` on six of them and `NULL` on three.
The filter matched zero rows for every list, so no site-scope request was ever
honoured — the failure was total, not partial.

Send records are pruned after 30 days (`prune_sent_notifications`), so the
retained records reach back only to 2026-06-29 — one month of a roughly
three-and-a-half month exposure window. Recipient counts for the earlier part of
the window cannot be reconstructed from the database at all, which is why the
access-log route below is the only way to size the affected group.

The 138 recorded unsubscribes are requests made through the two working links.
They are evidence that the majority of opt-out paths functioned; they are not a
count of affected people.

## Why affected individuals cannot be identified from the system

The view performed a filtered `UPDATE` that matched nothing and wrote no other
record — no log row, no audit entry, no state change of any kind. A request that
did nothing is indistinguishable in the database from a request that was never
made.

Consequently the affected individuals cannot be enumerated from application data,
and their requests cannot be honoured retroactively from that source.

One external source was considered: the unsubscribe token is part of the URL
path, so production web server access logs contain
`POST /subscriptions/unsubscribe/<token>/site/<id>/` entries that map back to
individual subscribers.

Checked on 2026-07-29 with `scripts/incident-2026-07-28-scope-check.sh`. The
route is closed:

| | |
|:--|:--|
| Exposure window | 2026-04-16 to 2026-07-28 (~103 days) |
| Oldest retained access log | 2026-07-15 |
| Window covered by logs | 2026-07-15 to 2026-07-28 — 14 days, ~13% |
| Site-scope POSTs in the covered days | 0 |

Roughly 90 days of the window — 2026-04-16 to 2026-07-14 — predate the oldest
retained log and cannot be reconstructed from any source. Retroactive
identification is therefore not possible, and the fix-forward decision stands
by necessity rather than by choice.

The covered 14 days contain no unsubscribe requests of **any** scope — not the
broken site link, and not the two that worked. That was checked rather than
assumed, because zero across all three scopes has two very different
explanations: nobody used the links, or these logs do not capture the endpoint.

The database resolves it. Nine `ListSubscription` rows were deactivated in the
covered period, and every one is accounted for without an HTTP request:

| Deactivation | Rows | Cause |
|:-------------|:-----|:------|
| 2026-07-19 | 3 | bulk — all of one subscriber's subscriptions at once (admin action) |
| 2026-07-28 17:00 | 1 | Postmark 406 suppression |
| 2026-07-28 23:00 | 2 | Postmark 406 suppression |
| 2026-07-28 23:01 | 3 | Postmark 406 suppression |

None has the single-row shape a link click produces. The admin "Disable all
emails" action and the Postmark-406 suppression handler both deactivate every
subscription a person holds, in one transaction, with no request to the
unsubscribe endpoint — so the absence of POSTs in the logs is exactly what those
nine rows predict. The logs are sound; the period they cover simply contains no
link clicks.

That makes the zero result **uninformative rather than invalid**: nobody clicked
any unsubscribe link during the 14 logged days, so the sample says nothing about
the 89 unlogged ones. It must not be read as "nobody was affected".

Incidentally confirmed: the three suppression events are the P0 Postmark-406
handler working in production on the day it deployed.

## Categories of data involved

No personal data was disclosed, altered, lost, or made available to any
unauthorised party. The failure was that a request to stop processing was not
acted on, so the following continued to be processed for the affected
individuals:

- name and email address, for the purpose of sending newsletters
- list subscription state

## Remediation

Deployed 2026-07-28 — CI ships everything merged to `main`, so the exposure
window closed on that date. Detailed in
[subscriptions-p0-fix-plan.md](../subscriptions-p0-fix-plan.md), Task A:

- correct the view to filter on `Lists.site`, matching the field the footer link is generated from
- capture the number of subscriptions actually deactivated and pass it to the confirmation template, so a request that changes nothing can never again render a success message
- add regression tests covering the exact production data shape, including a list whose `Team.site` is `NULL`
- document the site-scope semantics in `docs/subscriptions.md`

The second item is the systemic control: it converts this class of silent
failure into a visible one.

## Decision and rationale

Fix forward, record the incident, do not contact recipients proactively.

Rationale:

- the affected individuals cannot be identified from application data, so targeted remediation is not possible from that source
- a broadcast "confirm your preferences" email would contact the entire subscriber base to reach an unknown subset, and would mail the people most likely to have wanted no further contact — increasing rather than reducing unwanted processing
- the two remaining unsubscribe links worked throughout, so every recipient retained a functioning means of opting out, including the global one
- affected individuals who still wish to opt out can do so through those links in any subsequent email

The residual risk accepted is that an unknown number of people continued to
receive newsletters after asking, through one specific route, to stop. Some may
have responded by marking the mail as spam; the system records suppressed
recipients arising from spam complaints and hard bounces, which is being
addressed separately as P0 finding 3 of the audit.

Legal classification of this incident is a matter for the data controller and has
not been asserted here.

## Open items

Run `scripts/incident-2026-07-28-scope-check.sh` on the production host. It is
read-only and covers the first two items below in one pass.

All resolved 2026-07-29 with `scripts/incident-2026-07-28-scope-check.sh`.

- ~~confirm the scope figures against the production database~~ — done, the figures above are production
- ~~establish whether production web server access logs cover the exposure window~~ — done. They do not: ~14 days retained against a ~103-day window, so ~90 days are unrecoverable and retroactive identification is not possible
- ~~record the base rate of the working unsubscribe links, to bound how likely it is anyone used the broken one~~ — done. Zero requests of any scope in the logged period, and all nine deactivations in that period are explained by admin action or Postmark suppression. The sample contains no link clicks at all, so it bounds nothing

No further action is possible on identification. Nothing here is outstanding.

## Follow-up raised, tracked separately

Access-log retention is ~14 days. That is short if logs are expected to serve as
an audit trail for anything — it is the reason this particular route closed.
Raised as an infrastructure question, not tracked in this record.
