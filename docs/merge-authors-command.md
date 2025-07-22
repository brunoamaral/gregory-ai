# Author Merge Command

This Django management command allows you to merge duplicate authors that have the same ORCID in the database.

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

## How It Works

1. **Find duplicates**: Searches for all authors with the given ORCID (both http and https variants)
2. **Select target**: Chooses which author to keep:
   - **Priority 1**: Authors with `https://orcid.org/` format
   - **Priority 2**: Most articles
   - **Priority 3**: Earliest created (lowest ID)
3. **Transfer articles**: Moves all article associations from duplicate authors to the target author
4. **Upgrade ORCID**: If keeping an author with `http://`, upgrades to `https://` if available
5. **Preserve data**: Updates the target author with any missing information from duplicates
6. **Clean up**: Deletes the duplicate author records

## Safety Features

- **Transaction safety**: All operations are wrapped in a database transaction
- **Dry run mode**: Preview changes without making them
- **Confirmation prompt**: Requires explicit "yes" confirmation
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

Example production workflow:
```bash
# 1. Preview the merge
python manage.py merge_authors 0000-0000-0000-1234 --dry-run

# 2. If satisfied with the preview, run the actual merge
python manage.py merge_authors 0000-0000-0000-1234

# 3. Verify the results in the admin interface or database
```
