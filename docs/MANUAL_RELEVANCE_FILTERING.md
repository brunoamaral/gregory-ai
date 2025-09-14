# Manual Relevance Filtering Implementation Summary

## Requirement
Articles should be excluded from weekly digest emails if they are manually tagged as not relevant for ALL subjects they are associated with in that specific digest list.

## Implementation Logic
For each article being considered for a digest list:

1. **Find intersection**: Get all subjects that the article is associated with AND that are also in the digest list
2. **Check manual tags**: For each of these subjects, check if there's a manual relevance tag
3. **Apply exclusion rule**: Exclude the article ONLY if:
   - There are manual relevance tags for the article-subject combinations, AND
   - ALL of those tags are marked as "not relevant" (is_relevant=False)

## Examples

### Digest List: Subjects A, B
- **Article 1**: Associated with A, B
  - Tagged: A=not relevant, B=relevant → **INCLUDED** (relevant for at least one subject)
  
- **Article 2**: Associated with A only  
  - Tagged: A=not relevant → **EXCLUDED** (not relevant for all its subjects in the list)
  
- **Article 3**: Associated with B, C
  - Tagged: B=not relevant, C=not relevant → **INCLUDED** (only B matters since C is not in the list; but B is not relevant so it would be excluded if no other subjects in the list)
  
- **Article 4**: Associated with A, B
  - Tagged: A=relevant, B=not reviewed → **INCLUDED** (relevant for A, not reviewed for B)

## Key Points
- Articles are only excluded if they have been explicitly reviewed and marked as not relevant
- If an article hasn't been reviewed for a subject (is_relevant=None), it's treated as potentially relevant
- Only subjects that are both in the article AND in the digest list are considered
- The exclusion is conservative: when in doubt, include the article

## Code Location
The filtering logic is implemented in:
- `send_weekly_summary.py` (lines ~142-175 for all-articles mode)
- `send_weekly_summary.py` (lines ~181-214 for standard mode)

Both modes use the same filtering logic to ensure consistency.

## Testing
Comprehensive Django tests have been created in:
- `subscriptions/tests/test_send_weekly_summary_manual_relevance.py`

The test suite includes:
- **TestManualRelevanceFiltering**: 10 test methods covering various scenarios
- **TestManualRelevanceFilteringEdgeCases**: 2 test methods for edge cases

### Test Scenarios Covered:
1. **Include when relevant for at least one subject**: Articles tagged as relevant for at least one subject in the list are included
2. **Exclude when not relevant for all subjects**: Articles tagged as not relevant for ALL their subjects in the list are excluded
3. **Exclude when not relevant for all list subjects**: Multi-subject exclusion logic
4. **Include when subject outside list is not relevant**: Subjects not in the digest list don't affect inclusion
5. **Include when not reviewed**: Unreviewed articles are included
6. **Mixed relevance scenarios**: Complex scenarios with various tag combinations
7. **Standard mode filtering**: Same logic applies in both all-articles and standard modes
8. **Debug output verification**: Debug mode shows detailed filtering information
9. **Empty manual tags**: No tags means all articles are included
10. **Partial manual tags**: Only some articles have tags
11. **Single subject list filtering**: Edge case with lists containing only one subject
12. **No articles scenario**: Graceful handling when no articles exist

## Usage
The functionality is automatically applied to all weekly digest emails. No additional configuration is required. The filtering respects the existing ML threshold and consensus settings while adding the manual relevance exclusion layer.
