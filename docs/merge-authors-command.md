# Author Merge Command

This Django management command allows you to merge duplicate authors that have the same ORCID in the database. The same merge mechanics are also available from the Django admin as a bulk action, for ad-hoc merges that aren't driven by a single known ORCID (see [Admin: Merge Selected Authors](#admin-merge-selected-authors) below).

## Purpose

In production, we sometimes get authors with the same ORCID because:
1. An author was initially created without an ORCID
2. Later, the ORCID was added to the author record
3. Meanwhile, another version of the same author was created with the ORCID from the beginning
4. **ORCID Protocol Issue**: Some authors have `http://orcid.org/ID` while others have `https://orcid.org/ID` for the same person

This creates duplicate author records that reference the same person, which need to be merged.

## ORCID Handling

The command intelligently handles different ORCID formats:

- **Input flexibility**: You can provide just the ID (`0000-0000-0000-1234`) or the full URL
- **Automatic search**: Searches for both `http://orcid.org/ID` and `https://orcid.org/ID` variants
- **HTTPS prioritization**: When merging, prioritizes keeping the author with the `https://` version
- **Automatic upgrade**: If keeping an author with `http://`, automatically updates to `https://` if available

## Usage

```bash
python manage.py merge_authors <ORCID> [OPTIONS]
```

### Arguments

- `orcid`: The ORCID to search for and merge authors (can be full URL or just the ID)

### Options

- `--dry-run`: Show what would be merged without making changes
- `--keep-author <ID>`: Specify the author_id to keep when merging (overrides automatic HTTPS prioritization)
- `--force`: Skip confirmation prompt and proceed with merge automatically

## Examples

### Dry Run (Safe Preview)
```bash
python manage.py merge_authors 0000-0000-0000-1234 --dry-run
```

You can also use full URLs:
```bash
python manage.py merge_authors "https://orcid.org/0000-0000-0000-1234" --dry-run
```

This will show you:
- All authors found with that ORCID (both http and https variants)
- Which author would be kept (✓ HTTPS versions are prioritized)
- Which authors would be merged/deleted
- What articles would be transferred
- Any ORCID upgrades that would happen

### Actual Merge
```bash
python manage.py merge_authors 0000-0000-0000-1234
```

This will:
1. Show all authors with the ORCID variants
2. Display the merge plan with HTTPS prioritization
3. Ask for confirmation
4. Perform the merge within a database transaction
5. Transfer all articles from duplicate authors to the kept author
6. Delete the duplicate author records

### Keep Specific Author
```bash
python manage.py merge_authors 0000-0000-0000-1234 --keep-author 42
```

This forces the command to keep author with ID 42 instead of auto-selecting based on article count.

### Force Mode (No Confirmation)
```bash
python manage.py merge_authors 0000-0000-0000-1234 --force
```

This will proceed with the merge without asking for confirmation. **Use with extreme caution** in production! Always run with `--dry-run` first to verify the merge plan.

You can combine flags:
```bash
# Force merge while keeping a specific author
python manage.py merge_authors 0000-0000-0000-1234 --keep-author 42 --force
```

## How It Works

1. **Find duplicates**: Searches for all authors with the given ORCID (both http and https variants)
2. **Select target**: Chooses which author to keep:
   - **Priority 1**: Authors with `https://orcid.org/` format
   - **Priority 2**: Most articles
   - **Priority 3**: Earliest created (lowest ID)
3. **Transfer articles**: Moves all article associations from duplicate authors to the target author
4. **Upgrade ORCID**: If keeping an author with `http://`, upgrades to `https://` if available
5. **Preserve data**: Updates the target author with any missing information from duplicates
6. **Move OpenAlex authorships**: The removed authors' `ArticleAuthorship` rows (author position and institutions on each article, see [02-sources-and-articles.md](02-sources-and-articles.md#author-positions-and-institutions-openalex)) move to the target author. Where the target already has a row on the same article, the target's row stays and the other is dropped. The target's `openalex_id` is then recomputed.
7. **Carry lab rows**: A manual lab membership (see the `Lab` and `LabMembership` tables in [02.1-database-tables-and-fields.md](02.1-database-tables-and-fields.md#lab)) wins over a computed one; when both are manual the target's stays. A lab headed by a removed author passes to the target, who becomes its head; when both head labs, the removed author's lab is merged into the target's (its members move over and are marked manual).
8. **Clean up**: Deletes the duplicate author records

## Safety Features

- **Transaction safety**: All operations are wrapped in a database transaction
- **Dry run mode**: Preview changes without making them
- **Confirmation prompt**: Requires explicit "yes" confirmation (can be bypassed with `--force`)
- **HTTPS prioritization**: Automatically prefers the more secure HTTPS ORCID format
- **Data preservation**: Keeps the best available information from all authors
- **Article deduplication**: Avoids creating duplicate article-author associations
- **Flexible input**: Accepts ORCID in any format (ID only, http://, or https://)

## Error Handling

The command will fail safely if:
- No ORCID is provided
- ORCID is empty or whitespace only
- ORCID doesn't exist in the database (in any variant)
- Only one author has the ORCID (no duplicates to merge)
- Database errors occur during the merge

All errors are reported clearly and the database is left unchanged.

## Sample Output

### Finding ORCID Variants
```
Found 2 authors with ORCID variants of 0000-0000-1234-5678:
✓ HTTPS | ID: 12345 | Name: John Smith             | Articles:   5 | ORCID: https://orcid.org/0000-0000-1234-5678
  HTTP  | ID: 67890 | Name: J. Smith               | Articles:   2 | ORCID: http://orcid.org/0000-0000-1234-5678

📌 Prioritizing HTTPS ORCID version: https://orcid.org/0000-0000-1234-5678

MERGE PLAN:
  KEEPING: John Smith (ID: 12345, Articles: 5)
           ORCID: https://orcid.org/0000-0000-1234-5678
  MERGING: J. Smith (ID: 67890)
           Merging ORCID: http://orcid.org/0000-0000-1234-5678
```

## Production Usage

Before running in production:

1. **Always run with --dry-run first** to preview changes
2. **Make a database backup** before running the actual merge
3. **Verify the results** after merging to ensure data integrity
4. **Use --force with extreme caution** - only after thoroughly testing with --dry-run

Example production workflow:
```bash
# 1. Preview the merge
python manage.py merge_authors 0000-0000-0000-1234 --dry-run

# 2. If satisfied with the preview, run the actual merge
python manage.py merge_authors 0000-0000-0000-1234

# 3. For automated scripts (use with caution!)
python manage.py merge_authors 0000-0000-0000-1234 --force

# 4. Verify the results in the admin interface or database
```

### ⚠️ Force Mode Warning

The `--force` flag bypasses the confirmation prompt and immediately proceeds with the merge. This is useful for:
- **Automated scripts** processing multiple ORCIDs
- **Batch operations** where manual confirmation is impractical
- **CI/CD pipelines** running data cleanup tasks

**However, use with extreme caution because:**
- There's no way to undo the merge
- No final chance to review the merge plan
- Database changes are permanent

**Always** run with `--dry-run` first to verify the merge plan before using `--force`.

## Admin: Merge Selected Authors

For one-off cleanups where you're browsing the Authors list rather than working from a known ORCID, the Django admin (`/admin/gregory/authors/`) exposes the same merge mechanics as a bulk action:

1. Select two or more authors in the changelist checkboxes.
2. Choose **Merge selected authors** from the actions dropdown and click **Go**.
3. On the confirmation page, pick which author to keep (defaults to the one with the most articles, then the lowest ID) and click **Merge authors**.

**ORCID compatibility check**: the action refuses to merge authors whose ORCIDs disagree. A blank ORCID is treated as compatible with any value, and comparison is normalized (`https://orcid.org/0000-0000-0000-1234`, `http://orcid.org/0000-0000-0000-1234`, and the bare `0000-0000-0000-1234` are all treated as the same ORCID) since stored values mix URL and bare-ID forms. If two selected authors have genuinely different non-blank ORCIDs, the action shows an error and makes no changes.

Once confirmed, the merge:
- transfers every article from the other authors onto the one kept (a few set-based queries on the `articles_authors` table), along with their OpenAlex authorship rows (the kept author's row wins on an article both have) and recomputes the kept author's `openalex_id`,
- carries lab memberships and labs across (a manual membership wins over a computed one, a lab headed by a removed author passes to the kept author or merges into theirs),
- moves the removed authors' author-outreach rows and contact opt-outs onto the kept author, and unions their `emails` into the kept author's (in order, case-insensitive),
- fills any blank `given_name`, `family_name`, `country`, or `orcid_check` field on the kept author from the others,
- stores the kept author's ORCID as the shared bare ID (no URL prefix),
- permanently deletes the other authors.

This shares its implementation (`gregory.services.author_merge.merge_authors`) with the `merge_authors` command above, so both paths behave identically — the admin action just adds ORCID-conflict validation and a `keep`-author picker UI on top for cases where you don't already know the ORCID to search for.

### Outreach rows and the outreach conflict

`AuthorOutreach` rows (the audit trail of the one-time author emails, see [author-outreach.md](author-outreach.md)) are protected from deletion and unique per site and author. `merge_authors` moves them to the kept author. If the kept author and a removed one (or two removed ones) each hold an outreach row for the **same site**, the merge cannot keep both rows, so it raises `OutreachConflictError` before changing anything. The admin action shows the message; the candidates screen below calls such a group **blocked**. `AuthorContactOptOut` rows (the "never contact" list) are repointed to the kept author instead of losing their author when the row is deleted.

Other tables that point at an author, and what a merge does with them: `articles_authors` (moved), `article_authorships` (moved, kept author's row wins on an article both have), `labs.head` and `lab_memberships` (see above), `AuthorOutreach` and `AuthorContactOptOut` (moved). `Articles` history does not track the `authors` link, so the set-based transfer loses no history.

## Author merge candidates (admin screen)

Superusers only: **Admin > Gregory > Author merge candidates** (other staff get a 403 and do not see it in the index). It lists the groups of author rows that share an OpenAlex author ID (`Authors.openalex_id`, filled by `fetch_openalex_authorships`), which usually means one person split into several rows. Nothing is stored for the groups: they are computed every time the page loads, in a fixed number of queries.

Each group is one of:

| Class | Meaning |
| --- | --- |
| **Clear-cut** | No ORCID conflict, no outreach conflict, every row has a family name and a given name, all rows have the same normalised family name (accents, case, hyphens and dots ignored), and every two given names agree: the same letters once spaces are dropped ("Jiawei" and "Jia Wei"), or name by name where an initial stands for a name and two or three letters can be glued initials ("M P", "MP", "Maria" and "Maria Pia" agree; "Jun" and "Jing", or "Leonard C" and "Leonard S", don't). The pipeline merges these without asking, so the rule leans towards review: on dev about 2% of groups that share a family name and first initial still go to review. |
| **Names differ** | No conflict, but the names do not fit that rule (for example "Samar Farouk Ahmed" and "S. Ahmed Farouk"). Read them before merging. |
| **Blocked** | Two different ORCIDs, or two rows with an outreach row for the same site. The reason is shown and the group cannot be merged from the screen or the command; fix the data first (for example remove a wrong ORCID) or leave it. |
| **Dismissed** | A group a superuser marked "Not the same person". |

Tabs show the count of each class. You can search by name, ORCID, OpenAlex ID or author ID. Groups are ordered by total article count, descending, 50 per page. Per row the screen shows the author ID (linked to the author page), name, ORCID, article count, whether emails are held, lab, and number of outreach rows.

- **Merge**: choose the survivor (radio; default is the row with an ORCID, then the most articles, then the lowest ID) and untick rows you do not want merged. The group is reloaded and re-checked first, so a blocked group is refused even from a stale page. It runs in one transaction, sets the survivor's ORCID to the shared one, and reports what moved.
- **Not the same person**: records a dismissal (with an optional note) for that exact set of author IDs. The group is hidden while it still has exactly those IDs; if a row joins or leaves, it shows again. **Show again** on the Dismissed tab removes the dismissal. Dismissals live in the `author_merge_dismissals` table and are not in the admin or API.
- **Merge all clear-cut groups**: a confirmation page with the count and a sample of 20, then merges up to 300 groups per click (stopping earlier if it nears two minutes, well under the gunicorn timeout) and reports how many are left; click again until none are. Dismissed, "names differ" and blocked groups are never bulk-merged.

A merged-away author ID simply stops existing: its page 404s. There is no redirect and no record of the merge.

## `merge_author_candidates` command and the pipeline

```bash
python manage.py merge_author_candidates [--dry-run] [--limit N]
```

Merges clear-cut, non-dismissed groups only (default `--limit 500` groups per run), each in its own transaction, and prints the groups merged, the author rows removed, the clear-cut groups left over the limit, and the groups skipped by reason (`names_differ`, `blocked`, `dismissed`). It takes a lock so two runs cannot overlap. `--dry-run` changes nothing.

`pipeline` runs it with `--limit 500` after `fetch_openalex_authorships` and before `rebuild_labs`, so duplicates the fetch just revealed are merged before labs are built. The cap means a large backlog (tens of thousands of groups) clears over a couple of weeks of twice-daily runs, unless a superuser clears it sooner with **Merge all clear-cut groups**.
