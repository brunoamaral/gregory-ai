# Unpaywall API Error Handling Fix

## Problem

When running the pipeline command, we encountered the following error:

```
Error decoding JSON from response for DOI: 10.1186/s13287-025-04457-5. Response: <!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
<title>404 Not Found</title>
<h1>Not Found</h1>
<p>The requested URL was not found on the server. If you entered the URL manually please check your spelling and try again.</p>
```

This error occurred because:

1. The `feedreader_articles` command was trying to check if articles are open access using the Unpaywall API
2. Some DOIs (like 10.1186/s13287-025-04457-5) returned a 404 Not Found response from Unpaywall
3. The code was trying to parse HTML error pages as JSON, causing the JSON decode error
4. The error messages were being printed to the console, but the pipeline continued processing

## Root Cause

The issue was in the `getDataByDOI` function in `unpaywall_utils.py`, which:
- Didn't specifically handle 404 responses
- Didn't have proper exception handling for request errors
- Was attempting to parse non-JSON responses as JSON

## Solution

We improved the error handling in the `getDataByDOI` function to:

1. Specifically check for 404 status codes and handle them gracefully
2. Add proper try/except blocks around the HTTP request to catch connection errors
3. Continue to log errors but in a more controlled way
4. Return empty dictionaries when errors occur so the pipeline can continue

## Testing

We created unit tests that verify:
- 404 responses are handled correctly
- Request exceptions are caught and handled
- Valid responses are still processed correctly

## Future Considerations

To further improve this code:

1. Consider implementing rate limiting to avoid overloading the Unpaywall API
2. Add caching for Unpaywall responses to avoid repeated lookups for the same DOI
3. Implement a fallback mechanism to check other sources when Unpaywall doesn't have data for a DOI
4. Add more detailed logging that captures statistics about success/failure rates
