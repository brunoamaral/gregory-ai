# EMA medicines

Gregory keeps a copy of the European Medicines Agency's list of centrally authorised
human medicines, so an editor can see where a medicine stands with the regulator: a
positive opinion, an authorisation, a new European Commission decision, a withdrawal. The
data is global reference data, like PubMed's publication types. It has no team, subject
or organisation of its own.

This first stage brings the data in and shows it in the Django admin. It does not yet link
medicines to categories or serve them through the API or RSS feeds.

## What the data is

EMA publishes every medicine it has handled in one public JSON file, refreshed twice a
day (06:00 and 18:00 Amsterdam time). It needs no key and no registration. See EMA's
page on [downloading website data](https://www.ema.europa.eu/en/about-us/about-website/download-website-data-json-data-format).
Gregory reads the human medicines and skips the veterinary ones.

One `Medicine` row is one EMA **product**, identified by its product number
(`EMEA/H/C/004043`), not one substance. The same substance is often several products with
different fates: Tysabri (the originator), Tyruko (a biosimilar) and Natalizumab Elan
Pharma (a refused application). The columns are described in
[02.1-database-tables-and-fields.md](02.1-database-tables-and-fields.md#medicines-ema).

Things worth knowing before using the dates:

- **`latest_ec_decision_date` is not the approval date.** It is the most recent European
  Commission decision, usually a variation such as a new indication. `authorisation_date`
  is the first approval. The admin labels the first "Latest EC decision".
- **The export says little about evaluation.** A medicine shows up at its CHMP opinion.
  `evaluation_start_date` and `rolling_review_start_date` are filled on a handful of rows.
- **`opinion_status` is short-lived.** EMA sets it only while a medicine is at the opinion
  stage and clears it once the Commission decides. See [Events](#events).
- **Substances.** `substances` is the INN split on `;`, lowercased. When EMA leaves the INN
  empty, the active substance is used. A few withdrawn applications have neither.
- **Status wording is EMA's own** and is stored as given. A value Gregory has not seen
  before is imported like any other.

## Syncing

```bash
docker exec gregory python manage.py sync_ema_medicines
```

`pipeline` runs it on every run (step 3d, after the patent importer). The download is one
request that sends `If-Modified-Since`, so a run on an unchanged file costs a `304` and
nothing else.

| Option | Meaning |
|:-------|:--------|
| `--file PATH` | Read a local export instead of downloading it. Skips the `If-Modified-Since` check. |
| `--dry-run` | Parse, compare and report; write nothing. |
| `--force` | Download even if EMA's `Last-Modified` has not moved. |

**Checks before writing.** The file must parse, hold as many rows as its
`meta.total_records` says, and not have lost more than 5% of its human rows since the last
accepted sync. A failed check stops the command with an error and changes nothing, so a
truncated or broken export cannot hide medicines.

**Writing.** All upserts and events go in one transaction. Existing rows are loaded once
and compared in memory, then written in batches. Rows missing from an export get
`in_latest_export = False`; they are never deleted, and a row that comes back is flagged
present again. The sync state (`EmaSyncState`) is updated only after a successful write.

**Output.** New, changed and unchanged medicines, rows no longer in the export, rows
skipped (veterinary), unparseable dates, and events by kind and basis. A bad date is
logged and stored as null; it never stops the run.

**First run.** Every medicine is new, so the sync writes `reconstructed` events from EMA's
date fields and no `observed` ones. Check the numbers against the export you expect.

## Events

`MedicineEvent` is the history people read; `Medicine` holds only the current state. An
event is **reconstructed** when it is read off a date field the first time a medicine is
seen, and **observed** when a later sync sees a difference.

- A date that appears or moves gives the event for that field, dated with EMA's date.
- `ec_decision` is recorded when the latest decision differs from the authorisation date.
  The export holds only the latest decision, so a first sync recovers one per product;
  later ones are observed as they arrive.
- `indication_changed` is dated the day the sync saw it and keeps both texts.
- `status_changed` is a safety net: it is recorded only when the status changed and no
  other event explains it.
- **The opinion outcome is inferred when EMA has cleared it.** If `opinion_status` is
  Positive or Negative it is used. Otherwise a product that was authorised had a positive
  opinion and a refused one a negative opinion. For anything else the event is
  `opinion_adopted`, which says an opinion was adopted without claiming its outcome.
  `opinion_status` going from a value to empty between syncs is EMA moving on, not an event.

The unique constraint on (medicine, kind, date) makes repeating a sync safe.

## In the admin

*Medicines* in the Django admin lists every medicine with its status, opinion status,
authorisation date and flags, filterable by status and the flags and searchable by name,
INN, active substance and product number. A medicine's page shows its events, newest first,
and a link to its EPAR page on EMA's site. It is read-only, and every staff user can browse
it, because the data is public and unscoped.

## Source and attribution

All of this is EMA's data. EMA's [legal notice](https://www.ema.europa.eu/en/about-us/legal-notice)
allows reuse, commercial included, provided EMA is acknowledged as the source. Wherever the
data is shown or served, credit "European Medicines Agency". Gregory stores only EMA's own
data fields, not the third-party documents its product pages link to.
