# Patents

Gregory tracks patent families for the subjects a team follows, so an editor can see the
IP picture next to the trials and papers already collected. Patents appear in the Django
admin and in the staff-only admin summary email, and leave the system through [XLSX
exports](#exporting) for staff and partners. They are not exposed through the API, RSS
feeds, MCP server or public newsletters.

## What a record is

One `Patents` row is one DOCDB *simple patent family*: all the published documents that
share the same priority filings (the EP application and grant, the WO publication, the
US and national filings of one invention). Every document is a `PatentPublication`
child row. The table fields are described in
[02.1-database-tables-and-fields.md](02.1-database-tables-and-fields.md#patents).

The title, abstract, claims, classification and inventors come from one *representative*
publication: the one OPS flags as representative, unless its patent office has no full
text in OPS, in which case the EP and then the WO member is preferred so that claims are
available. Claims exist in OPS only for EP, WO, CA and a set of European national
offices, so families filed only in the US, China, Japan or Korea have none.

Applicants reuse the `Sponsor` model (see `gregory/utils/patent_applicants.py`), so a
company that sponsors a trial and files a patent is one entity. An applicant who is also
listed as an inventor is treated as an individual: the raw name is kept on the
`PatentApplicant` row and no sponsor is created. New sponsors created from patents get a
name-keyword `sponsor_type` with source `rules`, which trial evidence overrides later.
Patent-only sponsors do not show up in `/sponsors/`, which lists sponsors through the
trials in scope.

## Setting up a source

1. **Credentials.** Register at the [EPO developer portal](https://developers.epo.org/user/register)
   (account type "Non-paying"), add an app under "My Apps", and enter the consumer key
   and secret in the Django admin under the organisation's *Credentials*
   (`epo_ops_consumer_key`, `epo_ops_consumer_secret`). One account serves every source
   of the organisation. Never put them in the repository or an `.env` file.
2. **Source.** In the admin create a Source with *Source type* "EPO patent search", a
   team and a subject, and a CQL query in *EPO search query (CQL)*. Leave out any
   publication-date clause; the importer adds its own window, and the form rejects a
   query that has one. For example:

   ```
   ta="multiple sclerosis" and cpc=/low A61P25/00
   ```

   `ta` searches the English title and abstract and `cpc=/low` includes every subgroup
   of the CPC symbol. Restricting to title/abstract and a drug class (A61P) keeps
   disease names that only appear as boilerplate in unrelated patents out of the
   results. A subject with no patent Source simply has no patents.

## Importing

```bash
docker exec gregory python manage.py feedreader_patents_ops
```

| Option | Meaning |
|:-------|:--------|
| `--source-id N` | Only process this Source. |
| `--since YYYY-MM-DD` | Backfill: start the window at this date instead of the incremental anchor. |
| `--max-families N` | Stop after creating N new families. The anchor does not advance. |
| `--dry-run` | Search and count; fetch no family data and write nothing. |

`pipeline` runs it on every run with `--max-families 500`, a safety ceiling for a new or
unusually broad source; a capped run keeps its anchor and carries on next time. Backfills
(`--since`) are run by hand. After category matching, `pipeline` also runs
`detect_patent_trial_links` over all patents, so links follow trials that finish or gain a
category.

**Window.** From `last_successful_fetch_at` minus 14 days up to today, split into monthly
slices (`pd within "YYYYMMDD YYYYMMDD"`). With no anchor and no `--since` it looks back 90
days. OPS returns at most 2,000 results per query, so a slice with more hits is halved
until it fits. If a single day still exceeds the cap the run is marked capped.

**Anchor.** `last_successful_fetch_at` only advances when every slice completed, none was
capped, no quota error occurred, every new family's bibliographic data arrived, and the
`--max-families` ceiling was not reached. A partial run is simply repeated; known
publications are skipped, so repeating is cheap.

**When OPS fails.** OPS answers part of its traffic with HTTP 500 when it reports itself
overloaded. Every request that gets a 500, 502, 503, 504 or 408, or loses its connection,
is retried after 10, 30, 60 and 120 seconds. A monthly search that still fails is skipped
and the run carries on with the next month; a family or publication that fails is skipped
the same way. Either keeps the anchor back, so the next run retries it. Three failed
monthly searches in a row stop the run: OPS is down, so try again later. A quota error
always stops the run.

A backfill is finished when a run ends without the line "was not complete; not advancing
the incremental anchor". "Created 0" alone does not mean that: a run that failed early
also creates nothing.

**Per result.** A publication already stored is skipped (and its family re-synced when
the `family_next_check` back-off is due). A new publication of a known family is added to
it. An unknown family is fetched from the OPS family service (members are filtered to the
simple family of the search hit, since OPS returns the larger INPADOC family), the
representative's bibliographic data is fetched in bulk (100 per request) and claims are
fetched only where OPS has them. A publication OPS now places in a different family from
the one stored is logged as a conflict and never moved.

**Quota.** OPS is free up to 4 GB a week with an hourly cap. The importer shares one
throttle history per host (`EPO_OPS_THROTTLE_DB`) because OPS throttles per IP. When the
quota is exhausted (HTTP 403 with `X-Rejection-Reason`, or 429) the run stops and
reports; rerun after the reset. The summary line reports requests and megabytes
received.

## Sponsor-timing signal

The question patents are collected to answer is whether a trial sponsor files a patent
while one of its trials is ongoing or recently closed. `detect_patent_trial_links`
materialises the answer as `PatentTrialLink` rows:

```bash
docker exec gregory python manage.py detect_patent_trial_links --recent
```

For every patent with a resolved applicant sponsor it compares the patent's earliest
priority date (the date the sponsor first filed) with the window of each of that
sponsor's trials:

- **Start**: `date_enrollement`, else `published_date`.
- **End**: `primary_completion_date`, else `completion_date`. For a trial that is still
  not yet recruiting, recruiting, enrolling by invitation, active or suspended the window
  is open.
- `during`: the priority date is inside the window. `after`: it falls within 24 months
  (`--after-months`) after the end.
- Withdrawn trials, trials with no usable dates and priority dates outside the windows
  produce no link.

The patent and the trial must also share a subject. `basis="category"` means they also
share a team category (typically the molecule) and is the strong signal; `basis="subject"`
is noisy for sponsors with many trials and patents in one subject.

| Option | Meaning |
|:-------|:--------|
| `--recent --days N` | Only patents discovered in the last N days (default 30). |
| `--patent-id N` | One patent. |
| `--after-months N` | Length of the "after" window (default 24). |
| `--reset` | Delete the selected patents' non-suppressed links first. |
| `--dry-run` | Report without writing. |

Links that no longer qualify are removed on the next run. Links an editor suppressed in
the admin are never recreated, modified or removed. Two limits are inherent to patents:
applications publish about 18 months after the priority date, so a filing made during a
trial can take that long to show up; and a later PCT or regional filing does not move the
family's priority date.

The admin summary email gains a "Sponsors filing patents around their trials" section
listing links not yet sent to that subscriber for that list, discovered within the list's
look-back window (category basis first, at most 15, then a count of the rest). A link
counts for a list only when a subject shared by the patent and the trial is one of the
list's subjects.

## Administration

Patents are listed under *Gregory > Patents*, scoped to the organisations of the signed-in
staff member through their sources. Filters cover team, subject, source, category,
applicant type, grant and priority year. Importer-managed fields are read-only; editors
curate sources, teams and subjects and can assign categories. Staff can only pick their own
organisations' sources, teams, subjects and categories, and links to other organisations' rows
are hidden and kept on save; see [Admin visibility](06-organisations-teams-and-sites.md#sources-teams-and-subjects).

*Gregory > Patent-trial links* lists the detected signals grouped by patent. It defaults
to the category basis (a *basis* filter switches to subject-only or all), and the
*Suppress* action dismisses links. Staff only see links of patents from their own
organisations' sources.

## Exporting

`export_patents_xlsx` builds a workbook of patent families, one sheet per subject, for
people who want to work with the data outside the admin. It is meant for us and named
partners, not for publication: the EPO's OPS terms allow processed records in our own
products but not republishing "the data as such", so the About sheet credits the EPO and
says "For research use; do not republish the patent text".

```bash
docker exec gregory python manage.py export_patents_xlsx --subjects 1 --site brain-regeneration.com --output /tmp/patents.xlsx
```

| Option | Meaning |
|:-------|:--------|
| `--subjects 1,2` | Subject ids to export (or `--all-subjects`). |
| `--all-subjects` | Every subject, one sheet each. |
| `--team N` | Only subjects of this team. |
| `--site ID-OR-DOMAIN` | Required. The site the export is attributed to (publisher, licence and citation on the About sheet). |
| `--output PATH` | Default `patents_export_YYYYMMDD.xlsx`. |

**Admin actions.** *Export selected to XLSX…* on the patents list and *Export patents of
selected links to XLSX…* on the patent-trial links list (distinct patents of the selected
links). Both ask which site to attribute the file to: staff see the sites of their own
organisations, superusers every site. "Select all" on a filtered list exports the whole
filtered set. For staff the file only contains what the admin shows them: their
organisations' subjects, teams, sources, categories and trial links. Selected families with
no visible subject go on a "No subject" sheet, so none disappears silently.

**Sheets**, in order:

| Sheet | Content |
|:------|:--------|
| About this file | Source, licence, citation, the EPO attribution and terms of use, and a summary of every sheet. |
| One per subject | One row per family, newest priority date first: identity, abstract, claims (cut off at Excel's 32,767-character cell limit; `claims_length` and `claims_truncated` show it), classification, inventors, applicants (raw name and matched sponsor), publication summary, subjects, teams, sources, categories and trial-link summary. A family in two subjects appears on both sheets. |
| No subject | Admin export only: selected families with no visible subject. |
| Trial links | One row per [sponsor-timing link](#sponsor-timing-signal), dismissed ones included and flagged, with the trial's details and its window (`trial_window_start`/`end`, computed by the detector's own function). |
| Publications | One row per published document of every exported family. |
| Categories | Each category's search terms and the patent matching settings. |
| Glossary | A description of every column. |
| Data source | A plain-language introduction to patents, kind codes, families, priority dates and the caveats of this data, for readers with no patent background. |

`utitle`, `usummary` and the refresh back-off fields are left out. A new field on `Patents`
is appended to the subject sheets automatically; the export tests fail until it has a
glossary entry. The code is `gregory/exports/patents_xlsx.py` (shared helpers with the
trials export are in `gregory/exports/xlsx_common.py`). Exports run synchronously; the dev
database (101 families) builds in under a second.

## Categories

`TeamCategory` has `match_min_score_patents` (default 3) and patent weights (title 3,
abstract 2, claims 1) alongside the article and trial ones. They are stored under
`match_weights["patent"]` and edited in the *Patent score weights* section of the
category admin.

Patents are matched by `rebuild_categories` alongside articles and trials (a patent must
share a subject with the category). `--patents-only` runs just the patent pass;
`--articles-only` and `--trials-only` skip it. Patent matching has its own configuration
fingerprint (`TeamCategory.patent_match_config_hash`), so changing a patent setting
re-matches patents only and never forces a full article and trial re-match. Manual
assignments are never removed.

A caveat for candidate evaluation: composition-of-matter patents filed before a drug has
an INN usually name it only by formula or company code, so category terms catch
second-medical-use and formulation patents (which name the drug) much better than the
original compound patent. Adding company codes to a category's terms helps.
