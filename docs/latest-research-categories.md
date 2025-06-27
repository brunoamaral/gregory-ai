# Latest Research by Category in Weekly Digest Emails

Gregory now supports displaying the latest research articles by team category in weekly digest emails. This feature allows users to stay up-to-date with the most recent research in specific categories, regardless of their relevance scores.

## Overview

The "Latest Research" section is displayed after the "Clinical Trials" section in weekly digest emails. It organizes articles by team category, showing just the title and DOI with a link to the original article.

## Configuration

1. In the admin interface, go to "Lists" and select a list that has "Weekly Digest" enabled.
2. In the "Latest Research Section" fieldset, select the team categories you want to include.
3. Save the list.

## Notes

- The latest research section is optional. If no categories are selected, the section will not appear in the emails.
- Articles are sorted by discovery date (newest first) within each category.
- Categories are displayed in alphabetical order.
- Only articles discovered in the last 30 days are included.
- Each category displays a maximum of 20 articles to avoid overwhelming recipients.

## Implementation Details

- The feature uses the TeamCategory model, with a many-to-many relationship from Lists to TeamCategories.
- Articles that belong to subjects within each category are retrieved and organized by category.
- Articles are rendered in a simplified format compared to the main articles section.
- A limit of 20 articles per category is enforced to maintain reasonable email size and improve user experience.
