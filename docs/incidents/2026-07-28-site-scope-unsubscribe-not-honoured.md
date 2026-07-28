# Data protection record: site-scope unsubscribe requests were not honoured

Record of a defect in the newsletter unsubscribe flow, kept for audit purposes.
Decision taken: fix forward, no proactive contact, document the incident.

Status: open — remediation not yet deployed.

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

Figures below are from the development database on 2026-07-28 and are indicative.
Production figures must be confirmed before this record is treated as final —
see open items.

| Measure | Value |
|:--------|:------|
| Distinct subscribers with a retained send record since 2026-06-21 | 155 |
| Total subscriber records | 535 |
| Subscribers holding at least one active subscription | 192 |
| Unsubscribe requests successfully recorded since 2026-04-16 | 138 |
| Subscribers known to have used the site-scope link | not recorded |

Send records are pruned after 30 days (`prune_sent_notifications`), so the
retained records reach back only to 2026-06-21. Recipient counts for the earlier
part of the window cannot be reconstructed from the database.

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

One external source may exist: the unsubscribe token is part of the URL path, so
production web server access logs would contain
`POST /subscriptions/unsubscribe/<token>/site/<id>/` entries that map back to
individual subscribers. Whether those logs are retained for the exposure window
has not been established — see open items.

## Categories of data involved

No personal data was disclosed, altered, lost, or made available to any
unauthorised party. The failure was that a request to stop processing was not
acted on, so the following continued to be processed for the affected
individuals:

- name and email address, for the purpose of sending newsletters
- list subscription state

## Remediation

Planned, not yet deployed. Detailed in
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

- confirm the scope figures against the production database and replace the indicative development figures above
- establish whether production web server access logs cover the exposure window. If they do, the site-scope requests can be identified by token and honoured retroactively, which would materially change the remediation and should be done in preference to fixing forward alone
- update this record with the deployment date of the fix, and change its status to closed
