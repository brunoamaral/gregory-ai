# BASE Search Feed Processor - Implementation Summary

## Overview
Added support for BASE (Bielefeld Academic Search Engine) RSS feeds to the GregoryAI article ingestion system.

## Implementation Details

### 1. New Processor Class: `BaseSearchFeedProcessor`
Location: `django/gregory/management/commands/feedreader_articles.py`

**Key Features:**
- Detects BASE search feeds by checking for 'base-search.net' in URL
- Extracts summary from RSS `summary` field (accepts truncated versions)
- Extracts DOI from `dc_relation` field with two methods:
  - Method 1: `doi:` prefix format (most common)
  - Method 2: `doi.org` URL format (fallback)
- No keyword filtering (as requested)
- No author extraction (as requested)

### 2. Processor Registration
Added to `Command.__init__()` feed processors list:
```python
self.feed_processors = [
    PubMedFeedProcessor(self),
    FasebFeedProcessor(self),
    BioRxivFeedProcessor(self),
    PNASFeedProcessor(self),
    SagePublicationsFeedProcessor(self),
    NatureFeedProcessor(self),
    BaseSearchFeedProcessor(self),  # NEW
    DefaultFeedProcessor(self),
]
```

## BASE Feed Structure (RSS 2.0)

### Key Fields:
- **title**: Article title
- **link**: Article URL (usually handle URL, not DOI URL)
- **published**: Publication date in GMT format
- **summary**: Abstract/summary (often truncated with trailing content)
- **dc_relation**: Contains DOI in format: `doi:10.xxxx/yyyy ; PMID`

### Example Entry:
```xml
<item>
  <title>Determinants of therapeutic lag in multiple sclerosis</title>
  <link>https://oskar-bordeaux.fr/handle/20.500.12278/139904</link>
  <pubDate>Fri, 3 Oct 2025 04:04:00 GMT</pubDate>
  <summary>Background: A delayed onset of treatment effect...</summary>
  <dc:relation>doi:10.1177/1352458520981300 ; 33423618</dc:relation>
</item>
```

## DOI Extraction Logic

The processor handles multiple DOI formats:

1. **Standard format**: `doi:10.1177/1352458520981300 ; 33423618`
   - Splits by semicolon
   - Checks for `doi:` prefix
   - Validates DOI starts with `10.`

2. **URL format**: `https://doi.org/10.1212/NXI.0000000000001003`
   - Regex extraction from doi.org URL
   - Pattern: `doi\.org/(10\.\d+/[^\s;]+)`

3. **Edge cases**:
   - Missing `dc_relation` field → returns `None`
   - Invalid DOI format → returns `None`

## Testing Results

### Test Coverage:
✅ URL detection (can_process)
✅ Basic field extraction (title, link, date)
✅ Summary extraction
✅ DOI extraction (both formats)
✅ Edge cases (missing fields, invalid formats)

### Test Output:
```
[Entry 1] Structural constraints of functional connectivity...
  ✓ Title: Structural constraints of functional connectivity...
  ✓ Link: https://oskar-bordeaux.fr/handle/20.500.12278/136468
  ✓ Published: 2025-10-03 04:04:00+00:00
  ✓ Summary (505 chars): Background: The relationship between...
  ✓ DOI: 10.1177/1352458520971807
```

All 5 test entries processed successfully with correct DOI extraction.

## Usage

### 1. Add BASE Search Source
In Django admin, create a new Source:
- **Method**: RSS
- **Source For**: Science Paper
- **Link**: `https://www.base-search.net/Search/Results?lookfor=YOUR_QUERY&type=all&l=en&oaboost=1&view=rss`
- **Active**: Yes

### 2. Run Feed Reader
```bash
docker exec gregory python manage.py feedreader_articles
```

The processor will automatically:
1. Detect BASE feed by URL
2. Extract article metadata
3. Fetch CrossRef data if DOI is present
4. Create/update articles in database
5. Link to teams and subjects

## Notes

- **Summary truncation**: BASE often truncates abstracts. We accept these truncated versions as-is (as requested).
- **Author extraction**: Not implemented in this processor (as requested). Authors will be fetched via CrossRef in the standard pipeline.
- **Keyword filtering**: Not implemented (as requested). All articles from the feed will be processed.
- **CrossRef integration**: When a DOI is found, the standard CrossRef enrichment pipeline will fetch full metadata including complete abstract, authors, publisher, etc.

## Files Modified

1. `django/gregory/management/commands/feedreader_articles.py`
   - Added `BaseSearchFeedProcessor` class (lines 331-377)
   - Registered processor in `Command.__init__()` (line 395)

## Files Created (for testing)

1. `test_base_processor.py` - Initial feed structure analysis
2. `django/test_base_integration.py` - Integration tests

## Verification

- ✅ Syntax check passed
- ✅ All integration tests passed
- ✅ Compatible with existing processor architecture
- ✅ No conflicts with other processors
