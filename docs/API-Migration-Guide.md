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

### Benefits of New Approach

1. **Unified Filtering**: All filtering options available on main endpoint
2. **Parameter Combinations**: Mix and match any filters (team + subject + author + search, etc.)
3. **Consistency**: Same endpoint for all article queries
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

## Response Headers

During the deprecation period, legacy endpoints will include these headers:

```http
X-Deprecation-Warning: This endpoint is deprecated. Use /articles/?team_id=1 instead.
X-Migration-Guide: /articles/?team_id=1
X-Deprecated-Endpoint: /teams/1/articles/
```

## Client Migration Checklist

- [ ] Identify all API calls using `/teams/{id}/articles/*` patterns
- [ ] Update URLs to use `/articles/?team_id={id}` format
- [ ] Test new endpoints to ensure equivalent functionality
- [ ] Update any documentation or client libraries
- [ ] Monitor deprecation headers in responses
- [ ] Consider taking advantage of new filtering combinations

## Enhanced Filtering Capabilities

The new unified endpoint supports all these parameters:

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

## Support

If you have questions about migration or need help updating your client code:

1. Check the API documentation at `/docs/API EndPoints.md`
2. Test both old and new endpoints during transition
3. Use the deprecation headers to identify which new endpoint to use

## Code Examples

### JavaScript/Node.js
```javascript
// Old approach
const oldUrl = `https://api.gregory-ms.com/teams/${teamId}/articles/subject/${subjectId}/`;

// New approach ✅
const newUrl = `https://api.gregory-ms.com/articles/?team_id=${teamId}&subject_id=${subjectId}`;

// With additional filters
const advancedUrl = `https://api.gregory-ms.com/articles/?team_id=${teamId}&subject_id=${subjectId}&search=${encodeURIComponent(searchTerm)}&ordering=-published_date`;
```

### Python
```python
# Old approach
old_url = f"https://api.gregory-ms.com/teams/{team_id}/articles/subject/{subject_id}/"

# New approach ✅
new_url = f"https://api.gregory-ms.com/articles/?team_id={team_id}&subject_id={subject_id}"

# With additional filters
import urllib.parse
search_term = urllib.parse.quote_plus("stem cells")
advanced_url = f"https://api.gregory-ms.com/articles/?team_id={team_id}&subject_id={subject_id}&search={search_term}&ordering=-published_date"
```

### cURL
```bash
# Old approach
curl "https://api.gregory-ms.com/teams/1/articles/subject/4/?search=regeneration&format=json"

# New approach ✅
curl "https://api.gregory-ms.com/articles/?team_id=1&subject_id=4&search=regeneration&format=json"
```

This migration will make the API more consistent, flexible, and easier to use!
