# PNAS RSS Feed Support

Gregory AI now supports importing articles from PNAS (Proceedings of the National Academy of Sciences) RSS feeds, which use the RSS 1.0/RDF format.

## How to Add a PNAS Feed

1. In the Django admin interface, go to the "Sources" section
2. Add a new source with the following configuration:
   - Title: [Descriptive title for the feed]
   - Team: [Select the appropriate team]
   - Subject: [Select the appropriate subject]
   - Link: The URL of the PNAS RSS feed (e.g., https://www.pnas.org/action/showFeed?type=etoc&feed=rss&jc=pnas&categoryId=11572)
   - Method: "rss"
   - Source for: "science paper"
   - Active: Check this box

## Technical Details

PNAS feeds are formatted as RSS 1.0 (RDF) and have some differences from typical RSS 2.0:
- The feeds use RDF (Resource Description Framework) namespace
- Article information is contained in `<item>` elements
- DOIs are stored in both `dc:identifier` and `prism:doi` fields
- Descriptions often contain both publication information and an abstract separated by `<br/>` tags

The `PNASFeedProcessor` class in `feedreader_articles.py` handles these PNAS-specific details to properly extract article information.

## Example PNAS Feeds

- Neuroscience: https://www.pnas.org/action/showFeed?type=etoc&feed=rss&jc=pnas&categoryId=11572
- Biochemistry: https://www.pnas.org/action/showFeed?type=etoc&feed=rss&jc=pnas&categoryId=11548
- Immunology and Inflammation: https://www.pnas.org/action/showFeed?type=etoc&feed=rss&jc=pnas&categoryId=11570

## Manual Testing

To test the PNAS feed processor:

```bash
cd django
python test_pnas_feed.py
```

This will create a test PNAS source and run the `feedreader_articles` command to fetch and process articles from the PNAS feed.
