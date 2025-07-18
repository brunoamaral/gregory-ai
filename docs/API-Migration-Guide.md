# API Migration Guide: From Team-Based URLs to Query Parameters

## Overview

The Gregory API is transitioning from team-based URL endpoints to a unified approach using query parameters. This change simplifies the API structure and provides more flexibility for filtering and combining parameters.

## Migration Timeline

- **Current Status**: Both old and new endpoints work
- **Phase 1**: Deprecation warnings added to legacy endpoints (✅ COMPLETED)
- **Phase 2**: TBD - Legacy endpoints will return deprecation notices in response bodies
- **Phase 3**: TBD - Legacy endpoints will be removed

## Endpoint Migrations

### Articles Endpoints

| Old Endpoint | New Endpoint | Status |
|-------------|--------------|---------|
| `GET /teams/{id}/articles/` | `GET /articles/?team_id={id}` | ⚠️ Deprecated |
| `GET /teams/{id}/articles/subject/{subject_id}/` | `GET /articles/?team_id={id}&subject_id={subject_id}` | ⚠️ Deprecated |
| `GET /teams/{id}/articles/category/{category_slug}/` | `GET /articles/?team_id={id}&category_slug={category_slug}` | ⚠️ Deprecated |
| `GET /teams/{id}/articles/source/{source_id}/` | `GET /articles/?team_id={id}&source_id={source_id}` | ⚠️ Deprecated |

### Subjects Endpoints

| Old Endpoint | New Endpoint | Status |
|-------------|--------------|---------|
| `GET /teams/{id}/subjects/` | `GET /subjects/?team_id={id}` | ⚠️ Deprecated |

### Authors Endpoints

| Old Endpoint | New Endpoint | Status |
|-------------|--------------|---------|
| `GET /teams/{id}/subjects/{subject_id}/authors/` | `GET /authors/by_team_subject/?team_id={id}&subject_id={subject_id}` | ✅ Migrated |
| `GET /teams/{id}/categories/{category_slug}/authors/` | `GET /authors/by_team_category/?team_id={id}&category_slug={category_slug}` | ✅ Migrated |

### Benefits of New Approach

1. **Unified Filtering**: All filtering options available on main endpoint
2. **Parameter Combinations**: Mix and match any filters (team + subject + author + search, etc.)
3. **Consistency**: Same endpoint for all article and subject queries
4. **Simplicity**: Fewer endpoints to maintain and document

## Migration Examples

### Basic Team Articles
```bash
# Old
curl "https://api.gregory-ms.com/teams/1/articles/?format=json"

# New ✅ Preferred
curl "https://api.gregory-ms.com/articles/?team_id=1&format=json"
```

### Team + Subject Articles
```bash
# Old
curl "https://api.gregory-ms.com/teams/1/articles/subject/4/?format=json"

# New ✅ Preferred  
curl "https://api.gregory-ms.com/articles/?team_id=1&subject_id=4&format=json"
```

### Complex Filtering (New Capability)
```bash
# This is only possible with the new approach
curl "https://api.gregory-ms.com/articles/?team_id=1&subject_id=4&author_id=123&search=stem+cells&ordering=-published_date&format=json"
```

### Team + Category Articles
```bash
# Old
curl "https://api.gregory-ms.com/teams/1/articles/category/natalizumab/?format=json"

# New ✅ Preferred (by slug)
curl "https://api.gregory-ms.com/articles/?team_id=1&category_slug=natalizumab&format=json"

# New ✅ Preferred (by ID)
curl "https://api.gregory-ms.com/articles/?team_id=1&category_id=5&format=json"
```

### Team + Source Articles
```bash
# Old
curl "https://api.gregory-ms.com/teams/1/articles/source/123/?format=json"

# New ✅ Preferred
curl "https://api.gregory-ms.com/articles/?team_id=1&source_id=123&format=json"
```

### Basic Team Subjects
```bash
# Old
curl "https://api.gregory-ms.com/teams/1/subjects/?format=json"

# New ✅ Preferred
curl "https://api.gregory-ms.com/subjects/?team_id=1&format=json"
```

### Enhanced Subjects Filtering
```bash
# Search subjects (new capability with enhanced filtering)
curl "https://api.gregory-ms.com/subjects/?team_id=1&search=multiple&ordering=subject_name&format=json"
```

### Authors Filtering (Migrated)
```bash
# Old authors by team and subject (no longer available)
# curl "https://api.gregory-ms.com/teams/1/subjects/1/authors/"

# New ✅ Preferred - Authors by team and subject
curl "https://api.gregory-ms.com/authors/by_team_subject/?team_id=1&subject_id=1"

# New ✅ Preferred - Authors by team and category (by slug)
curl "https://api.gregory-ms.com/authors/by_team_category/?team_id=1&category_slug=natalizumab"

# New ✅ Preferred - Authors by team and category (by ID)
curl "https://api.gregory-ms.com/authors/by_team_category/?team_id=1&category_id=5"

# New ✅ Enhanced - Authors with filtering and sorting (by slug)
curl "https://api.gregory-ms.com/authors/?team_id=1&subject_id=1&sort_by=article_count&order=desc"

# New ✅ Enhanced - Authors with filtering and sorting (by category ID)
curl "https://api.gregory-ms.com/authors/?team_id=1&category_id=5&sort_by=article_count&order=desc"
```

## Response Headers

During the deprecation period, legacy endpoints will include these headers:

```http
X-Deprecation-Warning: This endpoint is deprecated. Use /articles/?team_id=1 instead.
X-Migration-Guide: /articles/?team_id=1
X-Deprecated-Endpoint: /teams/1/articles/
```

## Client Migration Checklist

### For Articles
- [ ] Identify all API calls using `/teams/{id}/articles/*` patterns
- [ ] Update URLs to use `/articles/?team_id={id}` format
- [ ] Test new endpoints to ensure equivalent functionality
- [ ] Update any documentation or client libraries
- [ ] Monitor deprecation headers in responses
- [ ] Consider taking advantage of new filtering combinations

### For Subjects
- [ ] Identify all API calls using `/teams/{id}/subjects/` patterns
- [ ] Update URLs to use `/subjects/?team_id={id}` format
- [ ] Test enhanced search and ordering capabilities
- [ ] Update any documentation or client libraries
- [ ] Monitor deprecation headers in responses

### For Authors ✅ Migrated
- [x] Replace `/teams/{id}/subjects/{subject_id}/authors/` with `/authors/by_team_subject/?team_id={id}&subject_id={subject_id}`
- [x] Replace `/teams/{id}/categories/{category_slug}/authors/` with `/authors/by_team_category/?team_id={id}&category_slug={category_slug}`
- [x] Update to use enhanced filtering capabilities with `/authors/?team_id={id}&subject_id={subject_id}&sort_by=article_count`
- [x] Old endpoints removed and return 404
- [x] Test new endpoints for equivalent functionality

## Enhanced Filtering Capabilities

### Articles Endpoint
The new unified `/articles/` endpoint supports all these parameters:

- `team_id` - Filter by team (required for team-specific data)
- `subject_id` - Filter by subject (use with team_id)
- `author_id` - Filter by author
- `category_slug` - Filter by category slug
- `category_id` - Filter by category ID
- `journal_slug` - Filter by journal
- `source_id` - Filter by source
- `search` - Search in title and summary
- `ordering` - Order results (e.g., `-published_date`, `title`)
- `page` - Page number for pagination
- `page_size` - Items per page (max 100)

### Subjects Endpoint
The new unified `/subjects/` endpoint supports these parameters:

- `team_id` - Filter by team
- `search` - Search in subject name and description
- `ordering` - Order results (e.g., `subject_name`, `-id`, `team`)
- `page` - Page number for pagination
- `page_size` - Items per page (max 100)

## Support

If you have questions about migration or need help updating your client code:

1. Check the API documentation at `/docs/API EndPoints.md`
2. Test both old and new endpoints during transition
3. Use the deprecation headers to identify which new endpoint to use
4. Run the validation script: `python utils/validate_migration.py`

## Code Examples

### JavaScript/Node.js
```javascript
// Old approach - Articles
const oldUrl = `https://api.gregory-ms.com/teams/${teamId}/articles/subject/${subjectId}/`;

// New approach ✅ - Articles
const newUrl = `https://api.gregory-ms.com/articles/?team_id=${teamId}&subject_id=${subjectId}`;

// With additional filters
const advancedUrl = `https://api.gregory-ms.com/articles/?team_id=${teamId}&subject_id=${subjectId}&search=${encodeURIComponent(searchTerm)}&ordering=-published_date`;

// Old approach - Subjects  
const oldSubjectsUrl = `https://api.gregory-ms.com/teams/${teamId}/subjects/`;

// New approach ✅ - Subjects
const newSubjectsUrl = `https://api.gregory-ms.com/subjects/?team_id=${teamId}`;

// Enhanced subjects filtering
const advancedSubjectsUrl = `https://api.gregory-ms.com/subjects/?team_id=${teamId}&search=${encodeURIComponent(searchTerm)}&ordering=subject_name`;

// Old approach - Authors (no longer available)
// const oldAuthorsUrl = `https://api.gregory-ms.com/teams/${teamId}/subjects/${subjectId}/authors/`;

// New approach ✅ - Authors by team and subject
const newAuthorsUrl = `https://api.gregory-ms.com/authors/by_team_subject/?team_id=${teamId}&subject_id=${subjectId}`;

// New approach ✅ - Authors by team and category (by slug)
const newCategoryAuthorsUrl = `https://api.gregory-ms.com/authors/by_team_category/?team_id=${teamId}&category_slug=${categorySlug}`;

// New approach ✅ - Authors by team and category (by ID)
const newCategoryAuthorsByIdUrl = `https://api.gregory-ms.com/authors/by_team_category/?team_id=${teamId}&category_id=${categoryId}`;

// Enhanced authors filtering (by slug)
const advancedAuthorsUrl = `https://api.gregory-ms.com/authors/?team_id=${teamId}&subject_id=${subjectId}&sort_by=article_count&order=desc`;

// Enhanced authors filtering (by category ID)
const advancedAuthorsByCategoryIdUrl = `https://api.gregory-ms.com/authors/?team_id=${teamId}&category_id=${categoryId}&sort_by=article_count&order=desc`;
```

### Python
```python
# Old approach - Articles
old_url = f"https://api.gregory-ms.com/teams/{team_id}/articles/subject/{subject_id}/"

# New approach ✅ - Articles
new_url = f"https://api.gregory-ms.com/articles/?team_id={team_id}&subject_id={subject_id}"

# With additional filters
import urllib.parse
search_term = urllib.parse.quote_plus("stem cells")
advanced_url = f"https://api.gregory-ms.com/articles/?team_id={team_id}&subject_id={subject_id}&search={search_term}&ordering=-published_date"

# Old approach - Subjects
old_subjects_url = f"https://api.gregory-ms.com/teams/{team_id}/subjects/"

# New approach ✅ - Subjects  
new_subjects_url = f"https://api.gregory-ms.com/subjects/?team_id={team_id}"

# Enhanced subjects filtering
search_term = urllib.parse.quote_plus("multiple")
advanced_subjects_url = f"https://api.gregory-ms.com/subjects/?team_id={team_id}&search={search_term}&ordering=subject_name"

# Old approach - Authors (no longer available)
# old_authors_url = f"https://api.gregory-ms.com/teams/{team_id}/subjects/{subject_id}/authors/"

# New approach ✅ - Authors by team and subject
new_authors_url = f"https://api.gregory-ms.com/authors/by_team_subject/?team_id={team_id}&subject_id={subject_id}"

# New approach ✅ - Authors by team and category (by slug)
new_category_authors_url = f"https://api.gregory-ms.com/authors/by_team_category/?team_id={team_id}&category_slug={category_slug}"

# New approach ✅ - Authors by team and category (by ID)
new_category_authors_by_id_url = f"https://api.gregory-ms.com/authors/by_team_category/?team_id={team_id}&category_id={category_id}"

# Enhanced authors filtering (by slug)
advanced_authors_url = f"https://api.gregory-ms.com/authors/?team_id={team_id}&subject_id={subject_id}&sort_by=article_count&order=desc"

# Enhanced authors filtering (by category ID)
advanced_authors_by_category_id_url = f"https://api.gregory-ms.com/authors/?team_id={team_id}&category_id={category_id}&sort_by=article_count&order=desc"
```

### cURL
```bash
# Old approach - Articles
curl "https://api.gregory-ms.com/teams/1/articles/subject/4/?search=regeneration&format=json"

# New approach ✅ - Articles
curl "https://api.gregory-ms.com/articles/?team_id=1&subject_id=4&search=regeneration&format=json"

# Old approach - Subjects
curl "https://api.gregory-ms.com/teams/1/subjects/?format=json"

# New approach ✅ - Subjects
curl "https://api.gregory-ms.com/subjects/?team_id=1&format=json"

# Enhanced subjects filtering
curl "https://api.gregory-ms.com/subjects/?team_id=1&search=multiple&ordering=subject_name&format=json"

# Old approach - Authors (no longer available)  
# curl "https://api.gregory-ms.com/teams/1/subjects/1/authors/"

# New approach ✅ - Authors by team and subject
curl "https://api.gregory-ms.com/authors/by_team_subject/?team_id=1&subject_id=1"

# New approach ✅ - Authors by team and category (by slug)
curl "https://api.gregory-ms.com/authors/by_team_category/?team_id=1&category_slug=natalizumab"

# New approach ✅ - Authors by team and category (by ID)
curl "https://api.gregory-ms.com/authors/by_team_category/?team_id=1&category_id=5"

# Enhanced authors filtering (by subject)
curl "https://api.gregory-ms.com/authors/?team_id=1&subject_id=1&sort_by=article_count&order=desc"

# Enhanced authors filtering (by category ID)
curl "https://api.gregory-ms.com/authors/?team_id=1&category_id=5&sort_by=article_count&order=desc"
```

# Contacts

For any questions, please get in touch with bruno@gregory-ai.com.
