# EMA medicines

> The FDA's approvals share the same table, events and admin; see [fda-medicines.md](fda-medicines.md).
> This page describes the EMA side. The matcher links FDA medicines to categories too, with
> its own disease check (see [fda-medicines.md](fda-medicines.md#matching)); the API, the
> feeds and the MCP tools do not show FDA medicines yet.

Gregory keeps a copy of the European Medicines Agency's list of centrally authorised
human medicines, so an editor can see where a medicine stands with the regulator: a
positive opinion, an authorisation, a new European Commission decision, a withdrawal. The
data is global reference data, like PubMed's publication types. It has no team, subject
or organisation of its own.

Gregory brings the data in, links each medicine to the team categories it belongs to, shows
both in the Django admin, and serves them as RSS feeds of events (see [Feeds](#feeds)) and
through the JSON API (see [JSON API](#json-api)). The research assistant can search them and,
for editors, fix their links (see [MCP](#mcp)).

## What the data is

EMA publishes every medicine it has handled in one public JSON file, refreshed twice a
day (06:00 and 18:00 Amsterdam time). It needs no key and no registration. See EMA's
page on [downloading website data](https://www.ema.europa.eu/en/about-us/about-website/download-website-data-json-data-format).
Gregory reads the human medicines and skips the veterinary ones.

One `Medicine` row is one EMA **product**, identified by its product number
(`EMEA/H/C/004043`), not one substance. The same substance is often several products with
different fates: Tysabri (the originator), Tyruko (a biosimilar) and Natalizumab Elan
Pharma (a refused application). The columns are described in
[02.1-database-tables-and-fields.md](02.1-database-tables-and-fields.md#medicines-ema-and-fda).

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

## Matching medicines to categories and subjects

```bash
docker exec gregory python manage.py match_medicines
docker exec gregory python manage.py match_medicines --category 49 --dry-run
docker exec gregory python manage.py match_medicines --subject 13 --dry-run
```

`match_medicines` runs three passes, in this order, and adds, removes and relabels rows so
they agree with the rules below:

1. **Groups.** Copies of one medicine point at their lead (see
   [Grouping copies of a medicine](#grouping-copies-of-a-medicine)).
2. **Categories.** Every automatic category against every medicine.
3. **Subjects.** Every subject with EMA therapeutic areas against every medicine (see
   [Subject links](#subject-links)).

It runs at the end of every `sync_ema_medicines` that wrote something, and as pipeline step
7c, after `rebuild_categories` has picked up category edits. About 2,400 products against a
few hundred categories takes seconds, so there is no incremental window. `--category ID`
runs the category pass for that one category, and `--subject ID` the subject pass for that
one subject; each skips the other passes, and the two can't be combined. `--dry-run` reports
and writes nothing in any pass.

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
6. **Diagnostics.** A product whose ATC code starts with `V04` (diagnostic agents), `V08`
   (contrast media) or `V09` (diagnostic radiopharmaceuticals) never matches. We don't track
   diagnostics: in the Alzheimer's and Parkinson's areas that rules out Neuraceq, Vizamyl,
   DaTSCAN, Striascan and Celsunax. An automatic link to a diagnostic that already exists is
   removed on the next run, like any link that no longer matches; manual links are never
   touched. A new product with no ATC code yet isn't caught until EMA assigns one.

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

### Linking when a category is saved

A category edit takes effect straight away; an editor doesn't wait for the pipeline. When
an **automatic** category is created, or one of the fields the rule reads changes (terms,
excluded terms, subjects, type, modality, EMA substance patterns, *Include EMA combination
products*), its medicines are linked as part of the save. That covers the category form in
the admin (the admin reports "N medicines linked", and a failure shows a warning that the
pipeline will catch up) and the editor API behind the MCP `create_category` and
`update_category` tools. A failure never fails the save, and a manual category is skipped.
The links the save creates record no history, as with the pipeline's; a link it removes is recorded under the editor who saved. Changing *modality* from the
categories list page, rather than the category's own form, waits for the next pipeline run.

### Subject links

A medicine is also linked to the **subjects** whose condition it is approved for, with no
category needed. For every subject with *EMA therapeutic areas*, each non-diagnostic product
with a therapeutic area starting with one of them (same prefix rule as the
[disease check](#disease-check)) gets a row in `medicines_subjects`, with the matching area
in `matched_on`. Generics, biosimilars and grouped copies are linked too. A subject without
areas is a research area, not a condition, and gets no links; clearing a subject's areas
removes its links on the next run.

An editor takes a medicine that doesn't fit off a subject from the **Medicine subject links**
page in the admin: filter by subject, select the rows and run *Suppress selected links* (or
open one and tick *suppressed*). *Restore selected suppressed links* puts it back. The
change is recorded under the editor's name. The Editor role can change this page's rows, but
not the Subject page, which has the same list under *Medicines (EMA) approved for this
condition* for superusers and anyone given Subject change permission. Only *suppressed* is
editable either way: rows can't be added (the matcher derives them) or deleted (a deleted
row would come back on the next run). Staff see their organisations' links and change only
their own teams'. A suppressed link is never recreated, changed or restored. The two
suppressions are independent: suppressing a subject link doesn't remove the medicine from a
category of that subject, and the reverse.

### Grouping copies of a medicine

EMA often authorises one medicine under several names (Exelon, Prometax and Rivastigmine
Sandoz). Products that share their sorted `substances` **and** the same `indication` are
copies. The indication is compared lowercased, with the product's own name removed
(indications name their product: "Exelon is indicated for ...") and punctuation and
whitespace collapsed. One product in each group leads: an `Authorised` product first, then the
earliest `authorisation_date`, then the lowest EMA product number. The other products get
`grouped_under` set to the lead; leads and singletons get none. A group never mixes the two
regulators: an FDA product is grouped only with FDA products.

Substances alone would merge different medicines (Avonex and Rebif, Tecfidera and Skilarence),
so the indication has to match too. Generics, biosimilars, products never authorised (a refused
or withdrawn application is its own news) and products with no indication aren't grouped. The
rule is deliberately conservative: it only joins products whose texts are the same, so
Memantine Merz (a salt) stays apart from Ebixa. Groups are re-derived on every run.

### What the matcher touches

| Link | Matcher does |
|:-----|:-------------|
| Automatic, not suppressed | Creates it when missing, deletes it when it no longer matches, refreshes `matched_on` |
| Manual | Nothing. It skips every rule, the disease check included, so an editor can attach an off-label medicine on purpose |
| Suppressed | Nothing. Never recreated, changed or restored, even if the medicine stops matching |

The same holds for subject links: a non-suppressed row is created, deleted and relabelled to
agree with the subject's areas, and a suppressed row is never touched.

The matcher's creations and relabels are bulk writes and record no history, as with `rebuild_categories`. A removal is recorded: it names whoever made the request that ran the matcher (an admin or editor-API category save) and nobody for a pipeline run.

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
medicine's own page lists its links to the user's teams' categories and subjects (see
[In the admin](#in-the-admin)).

Editors get these permissions from the Editor role: full access to medicine–category links,
and view-only access to medicines and their events. The deploy runs `setup_roles`, so a
change to the role applies on its own. See [The Editor role](06-organisations-teams-and-sites.md#the-editor-role).

To give a broad category medicines, set its *EMA substance patterns* (`medicine_terms`) on the
category form. CAR-T uses `*cabtagene*` and `tisagenlecleucel`, which together find the
seven CAR-T products in EMA's export; a suffix such as `*leucel` would also catch Ebvallo and
Amtagvi, which are not CAR-T.

## Feeds

A site can publish the medicine events of its categories as RSS, so a partner hears about an
approval without polling anything. The site has to opt in: **`medicines_enabled`** is a
setting on the site (Site settings, *API visibility*) and is off by default, and the site's
`rss_enabled` has to be on as well. With either off the feeds are a 404. To turn a site on,
tick the box and save; no deploy is needed.

| Feed | URL |
|:-----|:----|
| Every category in the site's scope | `/feed/sites/{site_id}/medicines/` |
| One category | `/feed/sites/{site_id}/medicines/category/{category_slug}/` |
| One condition (subject), including untracked medicines | `/feed/sites/{site_id}/medicines/subject/{subject_slug}/` |

What a feed contains:

- **Category and site feeds: medicines the category links to** and an editor has not suppressed. A category counts for a
  site when at least one of its subjects is in the site's scope; one that is not is a 404.
- **No generics, biosimilars or duplicate products.** The feed carries news about the originator.
  A medicine EMA authorises under several names (Exelon, Prometax) is announced once, by its
  lead product, not once per name (see [Grouping copies of a medicine](#grouping-copies-of-a-medicine)).
- **The subject feed is the one feed that includes untracked medicines.** The first two feeds
  are curated: category links only. The subject feed also lists the medicines EMA approved for
  the subject's condition (a non-suppressed subject link) that no category tracks, so a site can
  follow a condition such as Alzheimer's before anyone has made a category for lecanemab. It
  lists a medicine with a live category link to a category of the subject too. Suppressing the
  subject link drops the medicine from the feed unless a live category link to that subject's
  category keeps it, the same rule as on the API. The subject has to be in the site's scope, or
  the feed is a 404.
- **Observed events, and recent reconstructed ones.** A reconstructed event is only listed when
  it is under 90 days old, because the first sync reconstructs every approval EMA has ever
  recorded. An event seen between two syncs is always listed.
- **The newest 50 events**, by event date.

An item is titled `Ocrevus (ocrelizumab): CHMP positive opinion`, links to the medicine's EPAR
page, and its text is the cleaned indication followed by `Source: European Medicines Agency`.
The wording of each kind of event lives in one place, `MedicineEvent.IN_WORDS`, which the API
uses for `kind_label`, so a kind is never worded two ways. The full reference, with the rest of the feed rules, is in
[03-api-and-rss-feeds.md](03-api-and-rss-feeds.md#medicine-feeds).

## JSON API

```
GET /medicines/                  the medicines, one per EMA product
GET /medicines/{id}/             one medicine, with its events
GET /medicines/events/           what happened to them, newest first
GET /categories/                 each category carries medicines_count
```

The parameters, fields and examples are in [03-api-and-rss-feeds.md](03-api-and-rss-feeds.md#medicines).
The rules that matter when reading or changing the code:

- **Two routes to visibility.** A medicine is shown when it has a link, which an editor has
  not suppressed, to a category with a subject in the caller's scope (the feeds' rule), **or** a
  [subject link](#subject-links) that is not suppressed to a subject in the scope, and only on
  sites with `medicines_enabled` on. The second route is what shows a medicine EMA approved for
  a condition before anyone has made a category for it. A site with the switch off gets an
  empty list, a 404 on a single medicine and a `medicines_count` of 0.
  `request.visible_medicine_subject_ids` holds the scope; the API's helpers are in
  `api/medicine_scope.py`. Both routes are `Exists()` conditions, never a join plus
  `distinct()`. The two suppressions are independent: a suppressed subject link next to a live
  category link leaves the medicine visible, and so does the reverse.
- **One link, not one join per filter.** `category_id` (one id or a comma-separated list),
  `category_slug`, `subject_id` and `site_id` go through `Exists()` conditions together with the
  visibility condition. With a category filter there is one `Exists()` over
  `MedicineCategoryAssignment`, so `?subject_id=1&category_id=6` means one live link to category
  6 *whose category has subject 1*. Without one, `subject_id` and `site_id` accept either a category
  link or a subject link, as two `Exists()` joined by OR, each holding every condition on its own
  link. A product reached by several links is still one row, and a link the caller cannot see
  or an editor suppressed never matches. A plain join per filter would answer a looser question
  and repeat products.
- **`tracked`.** `tracked=false` keeps medicines with no live link to a category the caller can see,
  `true` those with one. It is exactly "`categories` is empty", computed from the same
  `visible_links_queryset`, so a category the caller cannot see never decides it. Rows carry
  `tracked`, `subjects` (the visible subject links) and `grouped_under`; the detail adds
  `grouped_products`. The list's `count` is cached per query string, so `tracked=true` and
  `tracked=false` each have their own entry.
- **Generics, biosimilars and duplicate products are hidden** from both lists unless
  `include_generics=true`. A duplicate is a product with `grouped_under` set (see
  [Grouping copies of a medicine](#grouping-copies-of-a-medicine)). The single-medicine route
  returns them, since it is asked for by id. `medicines_count` leaves all three out too, so a
  category's number matches what `/medicines/?category_id=` returns.
- **`latest_ec_decision_date` is not the approval date.** It keeps that name in the API so
  nobody mistakes it for one; `authorisation_date` is the first approval.
- **Every row says where it comes from:** `"source": "European Medicines Agency"`, with the
  EPAR page as `url`. See [Source and attribution](#source-and-attribution).
- **No bulk export.** Both lists are JSON only; `all_results` is ignored.
- **Cost.** A list costs a fixed number of queries whatever the page holds: the visible category
  links and the visible subject links of the page's medicines come in one extra query each, and
  the events' medicines are joined.

## MCP

The research assistant (the MCP server, see [07-mcp-server.md](07-mcp-server.md#medicines)) can read
and curate medicines on the sites that have them.

| Tool | Who | What it does |
|:-----|:----|:-------------|
| `search_medicines` | Everyone | Searches the medicines filed under the site's categories or approved for one of its conditions by `name` (brand or product name), `substance` (the INN), category, subject, `status`, `opinion_status`, first-authorisation dates, `tracked` and `include_generics`. Compact rows with the dates, `tracked`, the categories, the subjects and the EPAR link |
| `get_medicine` | Everyone | One product with its events, each worded by `kind_label` |
| `set_medicine_link` | Editors with the edit scope | Puts a medicine in a category, or takes it out. Live immediately, applies to the whole category |

- **Per site.** The tools are listed, and can be called, only on a site with `medicines_enabled` on
  (the tenant directory, `GET /tenants/`, reports it). On any other site they do not exist and the
  site's instructions never mention medicines. `set_medicine_link` also needs an editor with the
  edit scope; a read-only sign-in never sees it.
- **Attribution travels with the data.** Every response says `source: "European Medicines Agency"`
  and that the list covers only centrally authorised medicines: a drug missing from it may still be
  authorised in individual countries, so "not in EMA's data" is not "not approved". The tool
  descriptions say the same, and tell the model to show the EPAR `link`.
- **`tracked=false` is the editor's to-do list.** It lists the medicines that show only because EMA
  approved them for one of the site's conditions. An editor tracks one by creating or updating a
  category (a matching term with an intervention modality); the instructions say so, and tell an
  editor not to use `set_medicine_link` for it, which only files a medicine that already has a
  category to be filed in.
- **`name` is for brand names, `substance` for the INN.** `name=ocrevus` is a case-insensitive
  substring of the product name (a former name in brackets counts); `substance=ocrelizumab` is an
  exact match on the active substance. A zero-hit response on one suggests the other.
- **`set_medicine_link` is the admin's fix, over MCP.** It calls
  `PUT`/`DELETE /editor/categories/{category_id}/medicines/{medicine_id}/`, which follows the table
  under [Fixing links](#fixing-links): `linked=false` suppresses an automatic link (the matcher never
  brings it back) and deletes a manual one; `linked=true` restores a suppressed link or adds a manual
  one. Repeating either changes nothing. The row is saved under the editor's attribution, so the link's
  history names the person and says `mcp`. Only a medicine the site can see, and a category the editor
  may change, are accepted; otherwise the answer is "not found". The route is described in
  [03-api-and-rss-feeds.md](03-api-and-rss-feeds.md#medicine-routes).
- **Restoring does not pin the link.** A restored automatic link is an ordinary automatic link again,
  so the matcher manages it from then on and removes it if the medicine stops matching the category.
  Only a pair with no link at all gets a manual one, which the matcher never touches. Nothing
  converts an existing automatic link to a manual one; if it keeps going missing, fix the
  category's terms or EMA substance patterns instead.

## In the admin

*Medicines* in the Django admin lists every medicine with its status, opinion status,
authorisation date and flags, filterable by status and the flags and searchable by name,
INN, active substance and product number. A medicine's page shows its events, newest first,
its links to the user's teams' categories and subjects, its group (the lead it is a copy of,
or the copies it leads), and a link to its EPAR page on EMA's site. It is read-only, and every
staff user can browse it, because the data is public and unscoped. The category and subject
links are the exception: they belong to teams, so they are scoped (see above).

Two filters on the list find the medicines nobody follows yet:

- **Tracked**: *Tracked (in a category)* keeps medicines with a live link to one of your
  teams' categories; *Untracked* keeps the rest.
- **Condition**: medicines approved for one subject's condition. It offers the subjects with
  EMA therapeutic areas that your teams own.

To find the candidates for a new category, set **Condition** to the subject (say
Alzheimer's) and **Tracked** to *Untracked*. When one is worth following, create or edit a
category for it: give it an intervention modality (or EMA substance patterns) and the
matching term, and the medicine is linked as soon as you save (see
[Linking when a category is saved](#linking-when-a-category-is-saved)).

## Source and attribution

All of this is EMA's data. EMA's [legal notice](https://www.ema.europa.eu/en/about-us/legal-notice)
allows reuse, commercial included, provided EMA is acknowledged as the source. Wherever the
data is shown or served, credit "European Medicines Agency". Gregory stores only EMA's own
data fields, not the third-party documents its product pages link to.
