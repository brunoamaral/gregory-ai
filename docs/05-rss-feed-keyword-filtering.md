# RSS Feed Keyword Filtering Guide

Gregory includes intelligent processing for RSS feeds with advanced keyword filtering capabilities. This feature allows you to automatically filter articles based on specific research interests.

## Overview

Gregory's feed processors for supported sources (bioRxiv, medRxiv, PNAS, etc.) apply keyword filtering to include only relevant articles. This helps reduce noise and focus on articles that match your research interests.

## How It Works

1. **Automatic Detection**: Gregory automatically detects supported feed URLs
2. **Keyword Parsing**: The system parses your keyword filter string into individual keywords and phrases
3. **Content Matching**: Each article's title and description are searched for keyword matches
4. **Case-Insensitive**: All matching is case-insensitive for better coverage
5. **Inclusion Logic**: Articles matching any keyword are included; others are filtered out

## Supported Feed Types

- **bioRxiv/medRxiv**: Any URL containing "biorxiv" or "medrxiv"
- **PNAS**: Any URL containing "pnas.org"
- Other feed processors can be easily added

## Configuring Keyword Filters

### Admin Interface

1. Navigate to `/admin/gregory/sources/` in your Django admin
2. Create a new source or edit an existing RSS source
3. Set the following fields:
   - **Method**: `rss`
   - **Source for**: `science paper`
   - **Link**: An RSS URL for your source
   - **Keyword filter**: Your comma-separated keywords and phrases

### Keyword Filter Format

The keyword filter field supports two types of entries:

#### Simple Keywords
Comma-separated individual terms:
```
epilepsy, alzheimer, parkinson
```

#### Quoted Phrases
Use quotes for exact multi-word phrases:
```
"multiple sclerosis", "traumatic brain injury", "alzheimer disease"
```

#### Mixed Format
Combine keywords and phrases:
```
epilepsy, "multiple sclerosis", neurodegenerative, "parkinson disease"
```

## Examples

### Neurological Research
```
epilepsy, "multiple sclerosis", alzheimer, parkinson, "traumatic brain injury", stroke, "brain tumor"
```

### Specific Disease Focus
```
"multiple sclerosis", "relapsing remitting", "progressive ms", demyelination, oligodendrocyte
```

### General Neuroscience
```
neuron, synapse, "synaptic plasticity", "neural network", cognition, memory
```

### Empty Filter
Leave the keyword filter field empty to include all articles from the feed (no filtering).

## Common bioRxiv and medRxiv RSS Feeds

Here are some popular bioRxiv and medRxiv RSS feeds you can use:

**bioRxiv Feeds:**
- **Neuroscience**: `https://www.biorxiv.org/rss/subject/neuroscience`
- **Cell Biology**: `https://www.biorxiv.org/rss/subject/cell-biology`
- **Biochemistry**: `https://www.biorxiv.org/rss/subject/biochemistry`
- **Molecular Biology**: `https://www.biorxiv.org/rss/subject/molecular-biology`
- **Genetics**: `https://www.biorxiv.org/rss/subject/genetics`

**medRxiv Feeds:**
- **Epidemiology**: `https://www.medrxiv.org/rss/subject/epidemiology`
- **Infectious Diseases**: `https://www.medrxiv.org/rss/subject/infectious-diseases`
- **Health Informatics**: `https://www.medrxiv.org/rss/subject/health-informatics`
- **Public and Global Health**: `https://www.medrxiv.org/rss/subject/public-and-global-health`
- **Neurology**: `https://www.medrxiv.org/rss/subject/neurology`

## Best Practices

### Keyword Selection
- Use specific terms related to your research area
- Include both common and scientific terminology
- Consider abbreviations and alternative spellings
- Use quoted phrases for multi-word concepts

### Testing Keywords
1. Start with a few specific keywords
2. Monitor the filtered results
3. Adjust keywords based on relevance
4. Add broader terms if too few results
5. Add more specific terms if too many irrelevant results

### Maintenance
- Review and update keywords periodically
- Remove keywords that generate too much noise
- Add new keywords as research interests evolve

## Technical Details

### Processing Logic
1. The system extracts the article title and description
2. Both are converted to lowercase for matching
3. Each keyword is checked against the combined text
4. If any keyword matches, the article is included
5. Non-matching articles are excluded and logged

### Performance
- Keyword filtering happens during feed processing
- Filtered articles are not stored in the database
- This reduces storage requirements and improves performance

### Logging
The system logs filtered articles for monitoring:
```
➡️  Excluded by keyword filter: [Article Title]
```

## Troubleshooting

### No Articles Found
- Check if keywords are too specific
- Verify the bioRxiv or medRxiv RSS feed is active
- Ensure the source is marked as active
- Review recent bioRxiv or medRxiv publications for keyword presence

### Too Many Irrelevant Articles
- Add more specific keywords
- Use quoted phrases for exact matches
- Remove overly broad terms
- Consider combining related terms

### Keyword Not Working
- Check for typos in keywords
- Ensure proper comma separation
- Verify quotes are properly closed
- Test with simpler keywords first

## Integration with Machine Learning

Articles passing keyword filtering are then processed by Gregory's machine learning models for relevance prediction. This creates a two-stage filtering system:

1. **Keyword Filtering**: Initial filtering based on explicit terms
2. **ML Prediction**: Secondary relevance assessment using trained models

This approach combines human expertise (keyword selection) with machine learning for optimal results.

## Performance Considerations

### Memory and Processing
- Keyword filtering happens during feed processing before database storage
- Filtered articles are not stored, reducing database size
- Processing is optimized for real-time feed updates

### Large Feeds
For feeds with many entries:
- Consider using more specific keywords to reduce processing load
- Monitor server resources during peak processing times
- Use caching mechanisms for frequently accessed feeds

### Optimization Tips
- **Start Specific**: Begin with narrow keywords and expand as needed
- **Monitor Results**: Track filtering effectiveness through logs
- **Regular Review**: Update keywords based on changing research focus
- **Batch Processing**: Use management commands for bulk operations

### Production Recommendations
- Schedule feed processing during off-peak hours
- Monitor keyword filter performance through Django admin logs
- Consider implementing feed-specific update frequencies
- Use database indexes on frequently queried fields

## Monitoring and Maintenance

### Log Analysis
Monitor the console output for filtering information:
```
# Processing articles from bioRxiv Neuroscience
  ➡️  Excluded by keyword filter: Cardiac function study
  ➡️  Excluded by keyword filter: Plant genetics research
```

Or for medRxiv:
```
# Processing articles from medRxiv Epidemiology
  ➡️  Excluded by keyword filter: Economic policy analysis
  ➡️  Excluded by keyword filter: Agricultural research study
```

### Performance Metrics
- Track the ratio of filtered vs. included articles
- Monitor feed processing times
- Analyze keyword effectiveness over time

### Regular Maintenance Tasks
1. Review keyword lists monthly
2. Update based on new research areas
3. Remove ineffective keywords
4. Add emerging terminology
