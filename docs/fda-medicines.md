# FDA medicines

Gregory also keeps the U.S. Food and Drug Administration's approvals, next to the
European Medicines Agency's (see [ema-medicines.md](ema-medicines.md)), so an editor can see
where a medicine stands with the FDA: approved, approved for a new use, discontinued,
revoked. FDA records are `Medicine` rows with `regulator = "fda"`; the EMA columns stay
empty on them, and the FDA columns stay empty on EMA rows. Like the EMA data they are global
reference data with no team, subject or organisation of its own.

> **Where this stands.** FDA medicines are imported, labelled, linked to categories, visible in
> the Django admin, and served by the JSON API, the RSS feeds and the MCP tools beside EMA's
> (see [API and feeds](#api-and-feeds) and [MCP](#mcp)).

## What the data is

Three public files cover what EMA publishes in one. None needs a key or a registration.

| Source | What it is | Used for |
|:-------|:-----------|:---------|
| [Drugs@FDA](https://open.fda.gov/apis/drug/drugsfda/) bulk file from openFDA | Every NDA, ANDA and CDER BLA, with its products and every approved submission. Refreshed on weekdays | One row per NDA or BLA, and its history |
| [Purple Book](https://purplebooksearch.fda.gov/downloads) monthly CSV | Every licensed biologic, from CDER and CBER | One row per CBER BLA (vaccines, blood products, cell and gene therapies, which Drugs@FDA lacks), and the biosimilar and interchangeable flags of the CDER BLAs |
| [DailyMed](https://dailymed.nlm.nih.gov/dailymed/) labels | The current label of each product, from the National Library of Medicine | The indication text, for the medicines that could belong in a category (see [Labels](#labels)) |

One `Medicine` row is one FDA **application** (`NDA022561`, `BLA761053`), not one brand
and not one substance. Its supplements are its history: a new indication is an efficacy
supplement on the same number. Some applications carry two brands for different diseases
(Kesimpta and Arzerra are both `BLA125326`); the row lists both in `brand_names`, and `name`
joins them with " / ". Ocrevus Zunovo (`BLA761371`) is its own application and its own row.

Things worth knowing:

- **Generics (ANDAs) are not imported.** They are about four in five of Drugs@FDA's
  applications, and a generic is not news. Biosimilars are BLAs and are imported, flagged
  `biosimilar`.
- **`authorisation_date` is the original approval**: the earliest approved original
  submission. For a CBER biologic it is the earliest approval date among the Purple Book's
  "Original" rows. An application with only a tentative approval has none.
- **The column meanings differ from EMA's.** `status` is `Approved`, `Tentatively approved`,
  `Discontinued` or `Revoked`. `holder` is the sponsor or applicant, `pharmacotherapeutic_group`
  the first openFDA pharmacologic class (`fda_pharm_classes` keeps them all), and
  `fda_original_class` the original submission's class (`TYPE 1` is a new molecular entity).
  See [02.1-database-tables-and-fields.md](02.1-database-tables-and-fields.md#medicines-ema-and-fda).
- **Substances.** `substances` holds the ingredient names lowercased, with FDA's four-letter
  suffix on biologics removed (`NATALIZUMAB-SZTN` becomes `natalizumab`). A CBER proper name
  is kept as one substance. Drugs@FDA sometimes writes a pair as one ingredient name
  (`OCRELIZUMAB AND HYALURONIDASE-OCSQ`, Ocrevus Zunovo): the name is split on the whole word
  AND into separate substances (`ocrelizumab`, `hyaluronidase`), and the row is flagged
  `is_combination`. A name with AND that is an enumeration (`LAMIVUDINE, NEVIRAPINE, AND
  STAVUDINE`) is split on its commas too; a name with commas and no AND is not. `inn` and `active_substance` keep the name as published. CBER proper names
  are never split.
- **<a id="substance-key"></a>`substance_key`.** The FDA names an ingredient as on its label, sometimes with the salt
  (Gilenya is `fingolimod hydrochloride`, EMA's is `fingolimod`), so exact `substances` do not pair the two
  regulators' records of one drug. `substance_key` does: each substance goes through `strip_salt`, which drops
  trailing salt, hydrate and counter-ion words (hydrochloride, tartrate, sulfate, monohydrate, sodium, ...) one at a
  time while more than one word is left, and stops when only a counter-ion would remain (`sodium chloride` stays).
  A short list of names keeps their last word (`dimethyl fumarate`, `diroximel fumarate`, `monomethyl fumarate`), and
  a metal that is the drug stops the stripping of the anion after it (`ferrous sulfate`, `silver nitrate`).
  A small `INN_ALIASES` map then turns the FDA's US names into the international ones EMA uses (`dalfampridine`
  to `fampridine`, `acetaminophen` to `paracetamol`, `epinephrine` to `adrenaline`, ...); add a pair only for the
  same molecule.
  A delivery enzyme listed as a second ingredient (hyaluronidase, as in Ocrevus Zunovo) is dropped when something
  else remains, as EMA files the subcutaneous form under the antibody. The rest is unique, sorted and joined with
  `+`. The sync sets it on every row it writes; `refresh_substance_keys` (pipeline step 3f, right after the syncs)
  recomputes every row and writes only the ones that changed, so editing the word lists in
  `gregory/utils/substance_key.py` reaches stored rows on the next run without a migration. The API filters
  `substance` (salt-tolerant) and `substance_key` (exact) use it; see
  [03-api-and-rss-feeds.md](03-api-and-rss-feeds.md#medicines). A new salt word that is missed shows up as an FDA
  key with no EMA partner: add the word and a test. `GET /medicines/drugs/` lists the records grouped by this
  key; an FDA-only drug is named after its first brand in normal case (`Zeposia`, not `ZEPOSIA`).
- **Brand names are as the FDA writes them**, in upper case for Drugs@FDA.
- **`indication` comes from the label.** It is empty until an application has been a candidate
  for a category and DailyMed holds a label for it (see [Labels](#labels)).
- **What the files do not cover.** Applications under review, review dates, designations,
  advisory committee votes, refusals (complete response letters are published apart and
  months late) and therapeutic areas. An FDA row that is missing says nothing about whether
  a drug is or is not approved.

## Syncing

```bash
docker exec gregory python manage.py sync_fda_medicines
```

`pipeline` runs it on every run (step 3e, after the EMA sync; step 3f, `refresh_substance_keys`, follows), with `--skip-follow-up`: the
pipeline's step 7c runs the labels and the matcher once, after `rebuild_categories`. Run by
hand, the command does both itself after a write. It never touches `indication`,
`label_indications`, `label_checked_at` or a CBER row's `url`: `sync_fda_labels` owns those. Each file is one conditional
request (`If-Modified-Since` and `If-None-Match`), so a run on unchanged files costs two
`304`s and nothing else. It is two halves, one per file, and they are independent: a
failed check on one stops that half, writes nothing for it and lets the other run, and the
command then exits with an error.

| Option | Meaning |
|:-------|:--------|
| `--file PATH` | Read a local Drugs@FDA file (the zip or the JSON) instead of downloading it |
| `--purplebook-file PATH` | Read a local Purple Book CSV instead of downloading it |
| `--purplebook-url URL` | Download this Purple Book file instead of the newest one on the downloads page |
| `--source drugsfda\|purplebook` | Run one half only. A single local file also runs just its half |
| `--dry-run` | Parse, compare and report; write nothing |
| `--skip-follow-up` | Do not run `sync_fda_labels` and `match_medicines` afterwards |
| `--force` | Download even if the file has not changed, and accept a file that is smaller than the last |

**Drugs@FDA.** The command reads the current file path from openFDA's download index
(`api.fda.gov/download.json`), downloads the zip (about 9 MB) and decodes one application at
a time, so the 124 MB of JSON is never in memory whole. It skips the ANDAs, parses each NDA
and BLA, and checks before writing: the JSON parses, the number of applications equals
`meta.results.total`, and the NDA and BLA count has not fallen more than 5% since the last
accepted sync.

**Purple Book.** The command finds the newest CSV on the downloads page (the month's case
varies in the file names). The file holds the month's changes under one header and the full
list under a second copy of it; only the rows after the last header are read, grouped by BLA
number (zero-padded to six digits). It stops when the columns are not the expected ones or
the CBER BLA count has fallen more than 5%. CBER BLAs become rows. For a CDER BLA the file
only sets `biosimilar` and `interchangeable` on the row Drugs@FDA made; changing those is not
an event. The halves run in that order, so the flags land on rows created in the same run: when
Drugs@FDA creates a BLA row, the Purple Book file is downloaded without conditional headers even
if it has not changed, so a new biosimilar is flagged at once instead of next month (the count
check still applies). A CDER BLA that is in the Purple Book but not in Drugs@FDA is not imported;
the summary counts them and lists the first 20. A CBER BLA that already has a Drugs@FDA row is
left to it.

**Writing.** Each half goes in one transaction. Existing rows are loaded once, compared in
memory, and written in batches of 500; a row counts as changed only when a stored field
differs. Rows are loaded without `raw`, and the sync compares `raw_digest` (a hash of it) instead. Rows missing from a file get `in_latest_export = False`, never deleted, and a row
that comes back is flagged present again. `FdaSyncState` (one row per file) is updated
only after a successful write. A bad date is logged and stored as null.

**Labels and matching.** After a write the command runs `sync_fda_labels` and then
`match_medicines`, so a new candidate is labelled and linked in the same run. A failure in
the label step is reported and the matcher still runs. `--skip-follow-up` skips both.

**First run.** Every row is new, so the sync reconstructs history from the FDA's dates: about
6,400 Drugs@FDA rows, about 380 CBER rows, and roughly 6,200 approvals, 5,100 efficacy supplements
and 130 tentative approvals as events. Check the summary against those.

## Labels

The FDA publishes no disease areas, so the question "is this medicine for multiple
sclerosis?" is answered from the label: the "Indications and usage" section of each DailyMed
label filed under the application.

```bash
docker exec gregory python manage.py sync_fda_labels
docker exec gregory python manage.py sync_fda_labels --dry-run
docker exec gregory python manage.py sync_fda_labels --application BLA125326
```

Downloading a label for every application would be about 3,000 labels and 700 MB, so only
**candidates** get one: FDA medicines that pass the pattern, combination and exclusion steps
(see [Matching](#matching)) for at least one automatic category with a subject that has
`fda_indication_terms`. A diagnostic is not a candidate. That is about 80 applications today.

For each candidate the command:

1. Asks DailyMed which labels the application has (one JSON request; an application can have
   up to ten, from repackagers and authorised generics).
2. Downloads a label's XML only when its `setid` is new or its `spl_version` changed, and keeps
   the stored entry for an unchanged label. A label that is no longer listed is dropped.
3. Stores `label_indications` (`setid`, `version`, `title`, `published`, `indication` per
   label), sets `label_checked_at`, and sets `indication` from the **main label**: the one
   whose title starts with a brand name (an earlier brand first), else the most recently
   published.
4. On a CBER row with no `url`, sets it to the main label's DailyMed page (the Purple Book has
   no per-product page).

Besides the candidates, the command refreshes **every FDA row that already has stored labels**
(`label_checked_at` set). A medicine that stops being a candidate (a category lost its terms
or its pattern) keeps its labels, and the category save hook links against them at once, so
they must not go stale. That adds a handful of list calls.

A DailyMed error on one application is reported, keeps that application's stored labels (and
its `label_checked_at`) and moves on; a label that fails to download is retried on the next
run. Every non-200 answer is an error, a 404 included. A 200 with an **empty list** for an
application that has stored labels is treated the same way (it is reported as "emptied
(kept)", and `label_checked_at` still moves): it is more likely a DailyMed hiccup than every
label withdrawn, and wiping them would make `match_medicines` delete the links. A label that
drops out while others stay listed is removed. The command never fails the run because of
DailyMed. It pauses 0.2 seconds between
requests and sends the importers' User-Agent.

| Option | Meaning |
|:-------|:--------|
| `--application NUMBER` | Refresh one application, whether or not it is a candidate |
| `--dry-run` | List the candidates and the labels that would be downloaded; download and write nothing |

The summary counts candidates (and the others refreshed for their stored labels), list calls,
labels downloaded, unchanged, failed, candidates with no label, and emptied (kept). `pipeline` runs it as step 7c, after `rebuild_categories` and
before `match_medicines`, so a category edit that creates new candidates gets their labels in
the same run. Saving a category in the admin or through the editor API relinks it with the
labels already stored and fetches nothing: a new FDA candidate links on the next pipeline run.

The first run downloads about 70 labels, roughly 17 MB; after that a run costs about 80 list
requests and the few labels that changed.

## Matching

`match_medicines` treats an FDA row like an EMA one, with two differences. The rest of the
rule (patterns, combinations, exclude terms, manual and suppressed links) is described in
[ema-medicines.md](ema-medicines.md#the-rule) and is the same.

- **Names.** A pattern matches a substance, the row's `name`, or any of its `brand_names`, so
  "Kesimpta" matches the Kesimpta / Arzerra row.
- **The disease check reads the labels.** The terms are the union of `fda_indication_terms`
  over the category's subjects. When it is empty the check is skipped, as for EMA. Otherwise
  one term must appear as a **whole word**, ignoring case, in the indications of **any** of
  the application's labels (not just the main one, which is what keeps Kesimpta linked
  through its shared application with Arzerra). A candidate with no label gets no automatic
  link. `matched_on` reads `ocrelizumab · multiple sclerosis`.

Why `fda_indication_terms` is not `ema_therapeutic_areas`: EMA's `Alzheimer Disease` never
appears in label text, which says "Alzheimer's disease" (with either apostrophe). The term
`alzheimer` matches both, and `parkinson` does not match "parkinsonism". Suggested values:
`multiple sclerosis`; `alzheimer`, `dementia`; `parkinson`. A data migration copies them from
`ema_therapeutic_areas` for those four areas when a subject has none set, so the first pipeline
run after the deploy does not link FDA candidates unchecked. Check them in the admin, and set
`parkinson` by hand if a Parkinson's subject has no EMA areas.

Other rules:

- **Diagnostics never link.** An FDA row is a diagnostic when one of its pharmacologic classes
  (`fda_pharm_classes`) contains "diagnostic" or "contrast", ignoring case. A row with no
  openFDA block (about 3,700 applications) is not caught.
- **Combinations.** Ocrevus Zunovo is one application whose ingredient the FDA writes as the
  single name "ocrelizumab and hyaluronidase-ocsq"; it is split into two substances and is a
  combination, so an `ocrelizumab` category links it only when it sets *Include combination
  products*, and then only if a label names the subject's disease.
- **No subject links.** An FDA medicine reaches a subject only through a category. Subject
  links would need a label for every active application.
- **Grouping.** `grouped_under` works within one regulator. On an FDA row the indication is
  the main label's, so in practice only rows with a label can group; biosimilars, rows with no
  approval date and rows with no indication never do. The lead is an `Approved` product first,
  then the earliest approval, then the lowest application number.

### Fixing links

Editors fix FDA links the same way as EMA links (see
[ema-medicines.md](ema-medicines.md#fixing-links)): suppress a wrong automatic link, or add one by hand. The category
page's picker offers FDA medicines too (search by brand name or application number), so an FDA
medicine whose label is missing (a discontinued application, say) can be added by hand. A
hand-made link is shown in the API and the feeds as soon as it is saved.

## Events

`MedicineEvent` is the history people read, as for EMA. Five kinds are new:

| Kind | In words | Set by |
|:-----|:---------|:-------|
| `fda_approved` | Approved by the FDA | The original approval, or a CBER BLA's first approval date. Once per application: a corrected date replaces the stored event, as a `reconstructed` one |
| `fda_tentative_approval` | Tentatively approved by the FDA | An original or an efficacy supplement with status `TA` (not a labeling or manufacturing supplement) |
| `fda_efficacy_supplement` | FDA approved a new indication or use | An approved supplement of class `EFFICACY` |
| `fda_discontinued` | Discontinued in the US | Observed only: every product became discontinued between two syncs |
| `fda_revoked` | US licence revoked | Observed only: a Purple Book licence became revoked or voluntarily revoked |

`status_changed` stays the safety net for any other status change, and an application that
goes from tentatively approved to approved gets the `fda_approved` event instead. Labeling,
manufacturing (CMC), REMS and other supplements are not events, and neither is a change of
label wording.

- The event date is the submission's status date, and `to_value` holds the submission number
  (`S-38`), so an editor can find it on Drugs@FDA. Two efficacy approvals on one day are one
  event, with both numbers.
- **First sync: reconstructed.** Every dated event of a new medicine is `reconstructed`.
- **Later syncs.** A dated event new to a stored medicine is `observed` when its date is
  within the last 90 days, otherwise `reconstructed`: Drugs@FDA sometimes lists an old
  submission late, and an old event marked `observed` would read as news. `fda_discontinued`,
  `fda_revoked` and `status_changed` are dated the day the sync saw the change.

The unique constraint on (medicine, kind, date) makes repeating a sync safe.

## API and feeds

`GET /medicines/` and `GET /medicines/events/` list both regulators' rows, and the three
medicine RSS feeds list both regulators' events. The full reference is in
[03-api-and-rss-feeds.md](03-api-and-rss-feeds.md#medicines); in short:

- **Visibility is EMA's.** A medicine is visible through a live category link to a category with
  a subject in the caller's scope, on a site with `medicines_enabled` on. One switch covers both
  regulators. FDA medicines have no subject links (see [Matching](#matching)), so they show through categories only, and `?subject_id=` finds them
  through their categories.
- **Source and regulator, per row.** `regulator` is `ema` or `fda`. `source` is `"U.S. Food and
  Drug Administration"` on an FDA row and `"European Medicines Agency"` on an EMA one, on
  medicines and events alike, and a feed item ends with `Source: U.S. Food and Drug
  Administration; label text from DailyMed (U.S. National Library of Medicine)` for an FDA event
  whose description carries the label's indication, and with the plain `Source: U.S. Food and Drug
  Administration` when it has none. `url` is the Drugs@FDA overview page, or a CBER biologic's
  DailyMed label.
- **`null` for the other regulator's fields.** An FDA row returns `null` for every EMA-only
  field (the product number, opinion status, therapeutic areas, ATC code, the designation
  flags, the opinion, EC, refusal, withdrawal, suspension and end dates, EMA's publication
  dates), and an EMA row `null` for the FDA-only ones (application number and type, centre,
  brand names, priority review, original class, interchangeable). `null`, not `false`, so a
  missing flag is never read as "no". `label_indications`, `fda_pharm_classes` and `raw` are
  not exposed; `indication` is the main label's text.
- **Filters.** `regulator=ema|fda` on both endpoints. The EMA-only filters (`opinion_status`,
  `advanced_therapy`, `orphan_medicine`) leave FDA rows out whenever they are used.
  `include_generics` still hides generics, biosimilars (Tyruko among them) and grouped
  copies. `ordering=-authorisation_date` is the regulator-neutral order; the default
  (`-ema_last_updated`) lists FDA rows after EMA's.
- **Feeds.** `?regulator=ema|fda` narrows any of the three feeds (anything else is a 404), and
  each query string is cached separately. The 90-day rule for reconstructed events applies to
  both regulators, which keeps the first sync's thousands of reconstructed approvals out of
  the feeds. An item's title reads `{first brand} ({ingredients}): {event in words}`.
- **`medicines_count`** on a category counts FDA links as well.
- **Attribution.** The API docs credit DailyMed for the label text and carry the openFDA
  disclaimer (not for clinical decisions; the FDA does not endorse Gregory's use of the data).

For the frontend: show the regulator on each row, group the two regulators' products of one
substance under that substance (they are separate records that are never grouped by Gregory),
and sort a mixed list by `authorisation_date`.

## MCP

`search_medicines`, `list_medicine_drugs`, `get_medicine` and `set_medicine_link` (see
[07-mcp-server.md](07-mcp-server.md#medicines)) work on both regulators' records, on the sites
that have `medicines_enabled` on.

- **`regulator`.** `search_medicines` takes `regulator` (`ema` or `fda`); omitted, it searches both.
  Every row and record has `regulator` and its own `source`, so the model can name the right
  regulator beside each medicine.
- **A medicine can appear twice**, once per regulator, as two records. The tool descriptions say so.
  With no `ordering` a search of both regulators (or of the FDA) is sent as `-authorisation_date`, so
  the two interleave; `regulator=ema` keeps `-ema_last_updated`.
- **Salt names.** The FDA names an ingredient as on its label, sometimes with the salt (Gilenya is
  `fingolimod hydrochloride`, EMA's is `fingolimod`; Zeposia `ozanimod hydrochloride`; Exelon capsules
  `rivastigmine tartrate`). The API's `substance` filter ignores salts and the US/international name
  difference (`dalfampridine` is `fampridine`), so `substance=fingolimod` finds both regulators' Gilenya, and
  records of one drug share `substance_key`. The tool descriptions, the zero-hit `guidance` and the site
  instructions say exactly that: `substance` matches with or without the salt and with the US or
  international name, every row carries `substance_key`, and the model groups by it (or calls
  `list_medicine_drugs`, which returns one entry per drug with both regulators' records in it) instead of
  trying the salt form or the brand.
- **Capitals.** The FDA's data writes brand names, ingredients and companies in capitals. The tool
  descriptions and the site instructions ask the model to present them in normal case; the data is not
  changed.
- **FDA rows show FDA fields.** A search row has `brand_names` and `fda_application_number`; the
  detail also has `fda_application_type`, `fda_center`, `fda_priority_review`, `fda_original_class`
  and `interchangeable`. The columns the API sends as `null` because they belong to the other
  regulator (EMA's opinion, European Commission and designation fields on an FDA record, the FDA's
  on an EMA one) are left out by the tools, so nothing prints as "None".
- **Coverage note.** An FDA record carries the FDA's note: approved new drugs and biologics (NDAs
  and BLAs) only, no generics (ANDAs) or applications under review, through openFDA with label
  text from DailyMed, not for clinical decisions, and "not in FDA's data" is not "not approved".
  A search for one regulator carries only that regulator's note; an unfiltered search carries both.
- **Visibility.** An FDA record shows only when it is filed under one of the site's categories
  (it has no subject links), so it is always `tracked`. `tracked=false` and `opinion_status` return
  EMA records only.
- **`set_medicine_link`** files an EMA or an FDA record in a category, or takes it out; the endpoint is
  the same for both.

## In the admin

*Medicines* lists both regulators, with a regulator column and filter, and searches the
application number and the brand names as well as the EMA fields. An FDA medicine's page shows
the FDA fields (application number, centre, brands, original class, priority review, pharmacologic
classes, biosimilar and interchangeable flags) and hides the EMA ones, with a link to its Drugs@FDA
page (a CBER biologic gets its DailyMed label), and a *DailyMed labels* block, read-only, with each
label's title, published date, version and indication text. An EMA medicine's page is unchanged.
The subject form has *FDA indication terms* next to the EMA areas. The category and subject link pages show the regulator next to each medicine.

## Source and attribution

openFDA data is public domain; the FDA asks for credit: "Data provided by the U.S. Food and
Drug Administration". openFDA also asks users not to rely on the data for medical decisions,
and each response carries a disclaimer to that effect. Wherever FDA data is shown or served,
credit "U.S. Food and Drug Administration". Purple Book data and DailyMed labels are FDA-
and NLM-published public documents.
