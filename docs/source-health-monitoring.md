# Source Health Monitoring Feature

This feature enhances the admin page for Sources to show information about the last time an article was published from each source. This helps identify inactive or problematic sources.

## Key Features

1. **Last Article Date**: Displays the date of the most recent article from each source and how many days ago it was published.
2. **Article Count**: Shows the total number of articles from each source.
3. **Health Status Indicator**: Visual indicator showing the health of each source:
   - Green dot: Healthy (content updated within the last 30 days)
   - Orange dot: Warning (30-60 days since update)
   - Red dot: Error (60+ days since update) 
   - Gray dot: Source is marked as inactive
   - Blue dot: No content yet (applies to both article and trial sources)

4. **Team-Based Access**: Users only see sources that belong to their team, while superadmins see all sources.
5. **Health Status Filter**: Filter sources by their health status to quickly identify problems.

## Implementation Details

- Added methods to the `Sources` model to retrieve latest article date and health status
- Created custom admin filter for source health status
- Updated the admin interface to display the new information
- Implemented team-based access controls
