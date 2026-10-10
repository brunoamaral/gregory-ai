# Patents

Gregory tracks patent families for the subjects a team follows, so an editor can see the
IP picture next to the trials and papers already collected. Patents are staff-only for
now: they appear in the Django admin and are not exposed through the API, RSS feeds, MCP
server or emails.

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
2. **Source.** In the admin create a Source with *Source for* `Patents`, *Method*
   `EPO Open Patent Services`, a team and a subject, and a CQL query in *EPO OPS CQL
   query*. Leave out any publication-date clause; the importer adds its own window. For
   example:

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

The command is not part of `pipeline` yet.

**Window.** From `last_successful_fetch_at` minus 14 days up to today, split into monthly
slices (`pd within "YYYYMMDD YYYYMMDD"`). With no anchor and no `--since` it looks back 90
days. OPS returns at most 2,000 results per query, so a slice with more hits is halved
until it fits. If a single day still exceeds the cap the run is marked capped.

**Anchor.** `last_successful_fetch_at` only advances when every slice completed, none was
capped, no quota error occurred, every new family's bibliographic data arrived, and the
`--max-families` ceiling was not reached. A partial run is simply repeated; known
publications are skipped, so repeating is cheap.

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

## Administration

Patents are listed under *Gregory > Patents*, scoped to the organisations of the signed-in
staff member through their sources. Filters cover team, subject, source, category,
applicant type, grant and priority year. Importer-managed fields are read-only; editors
curate sources, teams and subjects and can assign categories.

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
