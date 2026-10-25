# EMA medicines

Gregory keeps a copy of the European Medicines Agency's list of centrally authorised
human medicines, so an editor can see where a medicine stands with the regulator: a
positive opinion, an authorisation, a new European Commission decision, a withdrawal. The
data is global reference data, like PubMed's publication types. It has no team, subject
or organisation of its own.

Gregory brings the data in, links each medicine to the team categories it belongs to, and
shows both in the Django admin. It does not yet serve medicines through the API or RSS feeds.

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

**Matching.** After a successful write the sync runs `match_medicines`, so a sync that
changed substances or therapeutic areas relinks categories in the same run (see
[Matching medicines to categories](#matching-medicines-to-categories)). A `--dry-run`
skips it.

**First run.** Every medicine is new, so the sync writes `reconstructed` events from EMA's
date fields and no `observed` ones. Check the numbers against the export you expect.

## Events

`MedicineEvent` is the history people read; `Medicine` holds only the current state. An
event is **reconstructed** when it is read off a date field the first time a medicine is
seen, and **observed** when a later sync sees a difference.

- A date that appears or moves gives the event for that field, dated with EMA's date.
- **A corrected one-off date replaces its event.** Authorisation, refusal, application
  withdrawal, end of authorisation, and the start of a rolling review or evaluation
  happen once per product. If EMA moves one from a date to a different date, the event
  for the old date is deleted and the new one is recorded, so a product never shows two
  "Authorised" events. EC decisions, suspensions and opinions can recur (variations,
  re-suspension, re-examination), so a moved date adds a second event and keeps the first.
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

## Matching medicines to categories

```bash
docker exec gregory python manage.py match_medicines
docker exec gregory python manage.py match_medicines --category 49 --dry-run
```

`match_medicines` re-matches every category against every medicine, then adds, removes and
relabels links so they agree with the rule below. It runs at the end of every
`sync_ema_medicines` that wrote something, and as pipeline step 7c, after
`rebuild_categories` has picked up category edits. About 2,400 products against a few
hundred categories takes seconds, so there is no incremental window. `--category ID`
matches one category; `--dry-run` reports and writes nothing.

### The rule

Per category, skipping categories whose type is *Manual* (those only ever get links an
editor adds by hand):

1. **Patterns.** The category's `medicine_terms` when it has any. Otherwise its ordinary
   `category_terms`, but only when its modality is small molecule, antibody / biologic,
   cell / gene therapy or natural product. A research topic, rehabilitation, device or
   unclassified category gets no automatic links without `medicine_terms`.
2. **Substance.** A pattern matches one of the product's `substances` when it is equal, or
   equal plus one more word (`pixantrone` matches `pixantrone dimaleate`). A pattern with
   `*` must match the whole substance, with `*` standing for any run of characters:
   `*cabtagene*`, `*brutinib`. A pattern also matches the product's name exactly, which
   covers products with no substance. Everything is compared lowercased, with whitespace
   collapsed.
3. **Combinations.** A product with more than one substance is skipped unless the category
   ticks *Include EMA combination products*.
4. **Exclusions.** The category's *terms to exclude* rule a product out when one appears as
   a whole word in a substance or in the name (`*` is allowed).
5. <a id="disease-check"></a>**Disease check.** Take the union of *EMA therapeutic areas*
   over the category's subjects. If it is empty, skip this step. Otherwise keep a product
   only when one of its therapeutic areas starts with one of those headings, ignoring case.
   `Multiple Sclerosis` therefore covers `Multiple Sclerosis, Relapsing-Remitting` but not
   `Amyotrophic Lateral Sclerosis`.

The disease check is why sildenafil under an MS category does not link Viagra, and why a
CAR-T category on an MS subject links nothing: EMA approved those products for other
diseases. Put the category on a subject with no EMA areas and it links every product the
patterns match. When EMA later extends a medicine to MS, its areas change and the next run
links it, alongside the `ec_decision` and `indication_changed` events from the same sync.

`matched_on` records the pattern and the area that matched (`ocrelizumab · Multiple
Sclerosis`), or just the pattern when the disease check was skipped, so an editor can see
why a link exists.

Subject areas are filled in by hand, as the subjects' names vary between teams. Suggested
values: `Multiple Sclerosis` for MS-like subjects, `Alzheimer Disease` and `Dementia` for
Alzheimer's, `Parkinson Disease` for Parkinson's.

### What the matcher touches

| Link | Matcher does |
|:-----|:-------------|
| Automatic, not suppressed | Creates it when missing, deletes it when it no longer matches, refreshes `matched_on` |
| Manual | Nothing. It skips every rule, the disease check included, so an editor can attach an off-label medicine on purpose |
| Suppressed | Nothing. Never recreated, changed or restored, even if the medicine stops matching |

The matcher's writes are pipeline writes: no editor is recorded, as with `rebuild_categories`.

### Fixing links

Editors of the category's team work from the category's page in the admin, under *Medicines
(EMA)*:

- **Add** a medicine (search by name, substance or product number). It becomes a manual link.
- **Remove** a link with the delete box. A manual link is deleted. An *automatic* link is
  **suppressed** instead, because a deleted one would reappear on the next run. The change
  is recorded under the editor's name.
- **Restore** a suppressed link by clearing its *suppressed* box. To put back a medicine that
  was suppressed, restore the existing row; adding it again would collide with it.

*Medicine category assignments* in the admin lists every link, filterable by *suppressed*
and source, with *Suppress selected links* and *Restore selected suppressed links* actions
for bulk work. Staff see their organisations' links and change only their own teams'. A
medicine's own page lists its links to the user's teams' categories, and the medicine list
can be filtered to medicines with or without a link.

To give a broad category medicines, set its *EMA substance patterns* (`medicine_terms`) on the
category form. CAR-T uses `*cabtagene*` and `tisagenlecleucel`, which together find the
seven CAR-T products in EMA's export; a suffix such as `*leucel` would also catch Ebvallo and
Amtagvi, which are not CAR-T.

## In the admin

*Medicines* in the Django admin lists every medicine with its status, opinion status,
authorisation date and flags, filterable by status and the flags and searchable by name,
INN, active substance and product number. A medicine's page shows its events, newest first,
its links to the user's teams' categories, and a link to its EPAR page on EMA's site. It is
read-only, and every staff user can browse it, because the data is public and unscoped. The
category links are the exception: they belong to teams, so they are scoped (see above).

## Source and attribution

All of this is EMA's data. EMA's [legal notice](https://www.ema.europa.eu/en/about-us/legal-notice)
allows reuse, commercial included, provided EMA is acknowledged as the source. Wherever the
data is shown or served, credit "European Medicines Agency". Gregory stores only EMA's own
data fields, not the third-party documents its product pages link to.
