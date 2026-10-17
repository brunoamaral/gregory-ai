# PubMed sources

A PubMed source runs a PubMed search on every pipeline run and keeps the papers that were
added to PubMed since the last run. It does what the PubMed RSS sources do, without their
limits.

| | PubMed RSS source | PubMed source (this page) |
|:--|:--|:--|
| How many papers | The newest 100 at most | Every paper created in the window |
| Abstract | Often cut short | Full, with its section labels |
| Authors | Added later from CrossRef | From PubMed, with ORCIDs when it has them |
| Extra data | None | PMID and PMCID, publication types, MeSH headings, author keywords, language |
| Retractions | Found later through CrossRef | Read from PubMed's record |
| Setup | Paste a feed URL | Write a search (below) |

Both kinds can run side by side. A paper found by both is stored once: the PubMed source
matches it by PMID, then DOI, then link, then title, and fills in what the feed left out. From then on the RSS source only fills gaps in that article (its URL, a missing summary or date, the source link): it never replaces the title or abstract that PubMed supplied. Two papers that share a generic title but have different PMIDs ("Erratum", "Correction") are kept apart.

## Add a source

1. In the admin, go to **Sources**, then **Add source**.
2. Choose the source type **PubMed search**.
3. Pick the team and subject. Everything the source brings in is tagged with them.
4. Enter the **PubMed search** (see below), and optionally some publication types to leave out.
5. Save. Gregory searches PubMed straight away to check the search and shows how many
   papers it found in the last 30 days, and how PubMed read it.

The first run looks back 30 days. After that each run covers the time since the previous
successful run, plus 2 days of overlap. A run that fails halfway doesn't move the start, so
the next run covers the same period again. There is no backfill of older papers.

## Writing the search

Write it as you would on pubmed.ncbi.nlm.nih.gov, and try it there first:

```
"multiple sclerosis"[MeSH Terms] AND (remyelination OR "myelin repair")
```

```
(alzheimer*[Title/Abstract]) AND (biomarker*[Title/Abstract] OR "blood test"[Title/Abstract])
```

Gregory wraps your search in brackets and adds the date itself, so **don't add a date
clause**: `[dp]`, `[pdat]`, `[crdt]`, `[edat]`, `[mhda]`, `[lr]`, `[Date - Publication]`
and similar tags, or "last 5 years". The form refuses a search that has one, because it
would narrow every run without anyone seeing it. A phrase in quotes, such as
`"last 6 months of life"[tiab]`, is not mistaken for a date. The form also refuses a search
with an unclosed bracket or quotation mark, because the date would then attach to only part
of it.

The window is on the date a record was **created in PubMed**, not the date the paper was
published. Recently published papers can still arrive weeks late, and a paper is kept when
it first appears in PubMed.

PubMed does not warn about a misspelled field tag, such as `[Titel/Abstract]`; it quietly
searches something broader. The check on saving shows PubMed's own reading of the search
("PubMed read it as: …"), so look at that line. If PubMed returns warnings about the
search, they are shown too.

## Leaving out publication types

The **Leave out these publication types** list removes records of those types, for example
Comment, Letter or Editorial. Excluding a type also excludes the types under it in PubMed's
hierarchy: excluding **Review** also excludes every type that sits under Review in the MeSH
tree, such as Systematic Review. Look a type up at
[MeSH publication types](https://www.nlm.nih.gov/mesh/pubtypes.html) to see what sits under
it before relying on it.

If the list is empty, an administrator has to run `manage.py sync_publication_types` once.

## When a search finds too much

If the check on saving finds more than 500 papers in the last 30 days, the admin shows a
warning. For scale, all the PubMed RSS sources together bring in about 1,400 papers a month.
A broad search is usually a missing `AND`, a field tag that matched everything, or a topic
that needs narrowing. Narrow it, or leave out publication types.

PubMed returns at most 9,999 results for one search. When a window reaches that, the
importer splits it in halves until each part fits. If a single day alone reaches it, the
source fails on every run until the search is narrowed, and the log says why.

## NCBI credentials

Searches go through NCBI's E-utilities. Without an API key they share one limit of 3
requests a second for the whole server; with a key it is 10 a second. Add the key, a contact
email and the tool name under **Organization credentials**. The admin warns when a PubMed
source belongs to an organisation without a key. Everything works without one, just slower.

## Running it by hand

```bash
docker exec gregory python manage.py feedreader_articles_pubmed
docker exec gregory python manage.py feedreader_articles_pubmed --source-id 12 --days 90 --dry-run
```

`--source-id` limits the run to one source, `--days` sets how far back a source with no
earlier run looks, and `--dry-run` searches and reports without writing anything or moving
the start date. It also runs as part of `manage.py pipeline`.

## Data from PubMed

PubMed data comes from the U.S. National Library of Medicine (NLM) at the National Center
for Biotechnology Information (NCBI). See
[NCBI's disclaimer and copyright notice](https://www.ncbi.nlm.nih.gov/About/disclaimer.html).
Abstracts may be copyrighted by their publishers.
