# Author labs

A guide for superusers. Labs group authors around a senior author, using the author positions that OpenAlex reports for each paper. Everything here lives in the Django admin and is visible to superusers only: no lab data appears in the API, the MCP server, the RSS feeds or any serializer, and editors see none of the lab screens, columns, filters or actions.

The tables are described in [02.1-database-tables-and-fields.md](02.1-database-tables-and-fields.md#lab). The two commands behind this are `fetch_openalex_authorships` (author positions and institutions) and `rebuild_labs` (the grouping); both run in the pipeline, in that order.

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

- **Filters**: subject, country, needs review, manual, and number of members (1, 2 to 5, 6 to 15, 16 or more).
- **Search**: lab name, head's name, institution name.
- **Choosing a subject** adds two columns, "Articles in subject" and "Relevant in subject", and sorts by relevant, highest first. Both count distinct articles in that subject that have at least one author who is the lab's head or a member (collaborators don't count). An article with two members counts once. "Relevant" is the same flag the authors API uses for its relevant count.

## The lab page

- **Edit** the name, head, institution and notes. Changing the name, head or institution makes the lab **manual**.
- **Changing the head** gives the new head a manual head membership in this lab and turns the old head into a manual member. The new head can come from another lab. An author who already heads another lab can't be made head here: open that lab, or merge the two.
- **Members**: an inline of the lab's members, with author, core or external, shared papers, last shared year, source, ORCID and whether we hold an email. Adding an author who is already in another lab moves them. Adding or changing a row makes it manual. The head is edited at the top of the page, not in this list.
- **Naming hints**: the head's three most common printed affiliations, which often name the department or lab.
- **Recent papers**: the ten latest papers with the head as last author, linked to their admin pages.
- **Probable duplicates**: people in the lab who share an OpenAlex author ID, with a link to the authors list filtered to those IDs, where the existing "Merge selected authors" action applies.
- **Collaborators**: collapsed and read-only, because a large lab can have over a hundred.

To add a lab, use **Add lab**. A head is required, and the head gets a manual head membership.

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
