# Author labs

A guide for superusers. Labs group authors around a senior author, using the author positions that OpenAlex reports for each paper. Managing labs happens in the Django admin and is for superusers only: editors see none of the lab screens, columns, filters or actions. Frontends can read labs through the API (see [In the API](#in-the-api)), scoped to the caller's site. The MCP server's read tools (`search_labs`, `get_lab`, and the `lab` on the author tools) show the same data on every site. No lab data appears in the RSS feeds or in any editor tool.

The tables are described in [02.1-database-tables-and-fields.md](02.1-database-tables-and-fields.md#lab). The two commands behind this are `fetch_openalex_authorships` (author positions and institutions) and `rebuild_labs` (the grouping); both run in the pipeline, in that order. A third, `rebuild_lab_topics`, runs later in the pipeline, after relevance is refreshed, and stores which subjects and categories each lab publishes in.

## What a lab is

A lab is a **head**, a senior author, plus the people who repeatedly publish with them. Every author row has at most one lab membership, with a role:

- **Head**: the author the lab is built around. A lab has one head.
- **Member**: someone who shares at least two papers with the head.
- **Collaborator**: someone who shares exactly one paper with the head and nobody else more. Collaborators are shown on the lab page but are not counted as members.

Members are shown as **core** when they share an institution with the head on at least half of the papers they share, **external** when they don't, and **unknown** when OpenAlex gave no institution for one side.

## How labs are computed

`rebuild_labs` reads every author's position on every paper and applies these rules:

1. Author rows that share an OpenAlex author ID are treated as one person, and every count below is per person. Duplicate rows of one person all land in the same lab.
2. Only papers with 2 to 30 linked authors that have a last author count. Larger papers are consortium papers and carry no signal about a lab.
3. A person is a head when they are last author on at least 3 such papers. The head of a manual lab is always a head.
4. A paper counts more the more recent it is: 1 plus 0.3 for each year after (current year minus 10). A lab someone published with recently outranks an older one.
5. Anyone who is not a head and has at least two articles in total joins the head with whom they have the highest weighted total of shared papers, as a member if they share at least two papers, as a collaborator if exactly one. Ties go to the head with more shared papers, then the more recent shared paper, then the lowest head author ID.
6. The lab's institution is the head's highest-weighted institution.

Someone you placed by hand as a member or collaborator, or pinned to no lab, never heads a computed lab. Otherwise the next rebuild would recreate a lab around a person you moved out of one.

## The Labs list

Open **Labs** in the admin. The columns are the lab name, head, institution, country, members as "core / external", collaborators, last-author papers, needs review and manual.

- **Filters**: subject, category, country, needs review, manual, and number of members (1, 2 to 5, 6 to 15, 16 or more). The category filter has a search box above its list, because there are many categories. Its list holds the categories that at least one lab links to.
- **Search**: lab name, head's name, institution name.
- **Topics** column: the lab's three best categories, best first, or a dash. It reads the stored category links (see below), hidden ones excluded.
- **Choosing a subject or a category** shows only the labs with a visible link to it, and adds three columns: "Articles in subject", "Relevant in subject" and "Recent in subject" (or the same three for the category). All three are sortable. The list is ranked by relevant papers, then papers, then lab ID. With both chosen, only labs linked to both show, both sets of columns appear, and the category's numbers rank them, since a category is the narrower choice.
- **What the numbers mean**: they are the stored counts of the link, from the head's and members' papers (collaborators don't count; a paper counts once per lab). "Recent" is the current calendar year and the four before it, plus papers dated later. **"Relevant in subject" changed meaning**: it used to count papers with the global relevant flag, which is true when a paper is relevant for any subject. It now counts papers relevant for that subject by a human or by AI, the rule behind the articles API's `relevant=true` filter, scoped to the one subject. For a category, a paper is relevant when it is relevant for any of the category's subjects. The numbers are as fresh as the last `rebuild_lab_topics` run, which the pipeline does each time.

## The lab page

- **Edit** the name, head, institution and notes. Changing the name, head or institution makes the lab **manual**.
- **Changing the head** gives the new head a manual head membership in this lab and turns the old head into a manual member. The new head can come from another lab. An author who already heads another lab can't be made head here: open that lab, or merge the two.
- **Members**: an inline of the lab's members, with author, core or external, shared papers, last shared year, source, ORCID and whether we hold an email. Adding an author who is already in another lab moves them. Adding or changing a row makes it manual. The head is edited at the top of the page, not in this list.
- **Subjects and Categories**: the lab's visible topic links, best first, with papers, relevant, recent, latest year and whether the link is computed or manual. The counts are read-only. See "Subjects and categories" below.
- **Hidden subjects** and **Hidden categories**: collapsed sections with the links you hid.
- **Topic counts last changed**, under "Computed data": the latest time a rebuild changed one of this lab's counts. A rebuild that finds nothing new writes nothing, so this can be old on a healthy lab.
- **Naming hints**: the head's three most common printed affiliations, which often name the department or lab.
- **Recent papers**: the ten latest papers with the head as last author, linked to their admin pages.
- **Probable duplicates**: people in the lab who share an OpenAlex author ID, with a link to the authors list filtered to those IDs, where the existing "Merge selected authors" action applies.
- **Collaborators**: collapsed and read-only, because a large lab can have over a hundred.

To add a lab, use **Add lab**. A head is required, and the head gets a manual head membership.

## Subjects and categories

`rebuild_lab_topics` links each lab to the subjects and categories its head and members publish in. A link exists when the lab has 3 or more papers in the subject or category. Links are stored, not worked out when you look, so the Labs list stays fast; they update once per pipeline run. The command only reads articles and their relevance: it changes no article, relevance flag, prediction or category assignment.

On the lab page you can correct them:

- **Add**: in the Subjects section pick a subject from the list, or search for a category, then save. The link is marked manual and stays even with fewer than 3 papers (its counts may be 0). Adding a link that is hidden brings it back.
- **Hide**: tick Hide on a row and save. The link moves to the collapsed Hidden section and shows nowhere else: not in the Labs list filters or the Topics column. A rebuild keeps updating its counts but never brings it back.
- **Unhide**: tick Unhide on a row of the Hidden section and save.
- **Reset**: tick Reset on any row and save. Both the manual and hidden marks clear. The next rebuild keeps the link only if it has 3 or more papers.

A subject or category can't be changed on an existing row: add the right one instead. Your changes are recorded in the link's history. When two labs are merged, manual and hidden marks carry to the lab that remains, and hidden wins.

### Top labs on subject and category pages

The change page of a subject or a category has a read-only **Top labs** section for superusers, after the page's fields. It lists the 25 best visible links, ranked by relevant papers, then papers, then link order: the lab, its head, institution, papers, relevant, recent and latest year. Under the table are the total number of visible links and a link to the Labs list filtered by that subject or category. A topic with no links says "No linked labs yet." Hidden links are in neither the rows nor the total.

The section shows only on a saved subject or category, never on the add page. Editors use these pages too, and they see no section and no lab data; the page runs no query on the lab tables for them, and they can save as before. Nothing in it can be edited: change links from the lab page.

## Fixing, pinning, merging and resetting

- **Fix a lab**: edit it as above. Manual edits survive every rebuild.
- **Place one author**: open the author. The **Lab** section holds the lab, role and core or external. Saving it makes the placement manual.
- **Remove a member from a lab**: on the lab's page, tick delete on their row and save. The author is pinned to no lab, so the rebuild doesn't put them back.
- **Pin an author to no lab**: remove them on the lab's page as above, or on the author page leave the lab empty and pick a role, or select authors in the list and use **Remove selected authors from their lab**. The rebuild will not put them back in a lab.
- **Place several authors**: select them in the authors list and use **Assign selected authors to a lab…**, then pick the lab and the role. Anyone who already has a placement is moved. Lab heads are skipped, with a message: change a head on the lab's page.
- **Merge labs**: select two or more labs and use **Merge selected labs**, then pick the one that survives. The others' memberships move to it as manual memberships, a head of a merged-away lab becomes a member, the other labs are deleted and the survivor becomes manual. It can't be undone.
- **Reset an author to computed**: delete the membership row in the author's Lab section. The next rebuild decides again.
- The authors list has a **lab** column and the filters **Has a lab** and **Probable duplicate (same OpenAlex author)**.
- An article's page shows the lab of its last author, linked.

Deleting a lab deletes its memberships, manual ones too. To retire a lab without losing its people, merge it.

## What a rebuild leaves alone

A rebuild never changes a manual membership, including a pinned "no lab". It never renames, re-heads, re-homes or deletes a manual lab, though it keeps that lab's computed members and counts current. It creates, updates and deletes computed memberships and non-manual labs freely. If a run would delete more than half of the existing labs it stops and says so, which catches an empty or half-loaded authorship table; `--force` overrides it.

One consequence to know about: an author with a manual membership that isn't a head never heads a computed lab. So when you move the head of a computed lab into another lab, the next rebuild deletes the old lab if it isn't manual and no manual membership points at it, and places its members again.

## Needs review

A lab gets **needs review** from a rebuild when it can't decide:

- "head no longer qualifies": a lab that is not manual has a head who no longer meets the thresholds, but manual memberships still point at it, so it was not deleted.
- "head was deleted": the head's author row no longer exists. The rebuild does not guess a new head. Set one on the lab's page.

The flag clears itself on a later rebuild once the reason no longer applies.

## In the API

`GET /labs/` and `GET /labs/{id}/` serve these labs to frontends; the parameters and fields are in [the API reference](03-api-and-rss-feeds.md#labs). A few things follow from the rest of this page:

- Only the topic links a caller can see are shown, and a lab with none of them is not listed at all. A link you hid in the admin never appears. A category link also needs every one of the category's subjects to be visible to the caller, because its counts cover all of them.
- `curated` is true for a manual lab: one a superuser created, named, re-headed, re-homed or merged. Computed labs, including ones that need review, are listed too; the review flag, its reason and the notes stay private.
- Collaborators are never shown, only the head and the members.
- `GET /authors/?include=lab` (also `/authors/{id}/` and `/authors/search/`) adds a short `lab` summary to each author: the lab's id and name, the author's role (head or member) and the head. It is `null` for a collaborator, an author in no lab, or a lab the caller cannot see, using the same visibility rule as `/labs/`.
