"""
Database optimization migration to add missing indexes for category queries.

This migration adds indexes that will prevent the hanging database queries
when counting articles, trials, and authors for categories.

Note: We use regular CREATE INDEX instead of CONCURRENTLY for tests and migrations.
For production, run the optimize_category_queries management command instead.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('gregory', '0021_authors_ufull_name_historicalauthors_ufull_name_and_more'),  # Update this to your latest migration
    ]

    # Use atomic=False to allow CONCURRENTLY index creation
    atomic = False
    
    operations = [
        # Add indexes for many-to-many relationship tables
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS idx_articles_team_categories_category_id ON articles_team_categories (teamcategory_id);",
            reverse_sql="DROP INDEX IF EXISTS idx_articles_team_categories_category_id;"
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS idx_articles_team_categories_article_id ON articles_team_categories (articles_id);",
            reverse_sql="DROP INDEX IF EXISTS idx_articles_team_categories_article_id;"
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS idx_trials_team_categories_category_id ON trials_team_categories (teamcategory_id);",
            reverse_sql="DROP INDEX IF EXISTS idx_trials_team_categories_category_id;"
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS idx_trials_team_categories_trial_id ON trials_team_categories (trials_id);",
            reverse_sql="DROP INDEX IF EXISTS idx_trials_team_categories_trial_id;"
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS idx_articles_authors_article_id ON articles_authors (articles_id);",
            reverse_sql="DROP INDEX IF EXISTS idx_articles_authors_article_id;"
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS idx_articles_authors_author_id ON articles_authors (authors_id);",
            reverse_sql="DROP INDEX IF EXISTS idx_articles_authors_author_id;"
        ),

        # Add indexes for date filtering (commonly used in category queries)
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS idx_articles_published_date ON articles (published_date);",
            reverse_sql="DROP INDEX IF EXISTS idx_articles_published_date;"
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS idx_articles_discovery_date ON articles (discovery_date);",
            reverse_sql="DROP INDEX IF EXISTS idx_articles_discovery_date;"
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS idx_trials_published_date ON trials (published_date);",
            reverse_sql="DROP INDEX IF EXISTS idx_trials_published_date;"
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS idx_trials_discovery_date ON trials (discovery_date);",
            reverse_sql="DROP INDEX IF EXISTS idx_trials_discovery_date;"
        ),

        # Add composite indexes for common query patterns
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS idx_team_categories_team_subject ON team_categories (team_id, id);",
            reverse_sql="DROP INDEX IF EXISTS idx_team_categories_team_subject;"
        ),

        # Add indexes for the team categories table
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS idx_team_categories_slug ON team_categories (category_slug);",
            reverse_sql="DROP INDEX IF EXISTS idx_team_categories_slug;"
        ),
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS idx_team_categories_team_id ON team_categories (team_id);",
            reverse_sql="DROP INDEX IF EXISTS idx_team_categories_team_id;"
        ),

        # Add covering index for articles with frequently accessed fields
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS idx_articles_covering ON articles (article_id, title, published_date, discovery_date);",
            reverse_sql="DROP INDEX IF EXISTS idx_articles_covering;"
        ),

        # Add covering index for trials with frequently accessed fields  
        migrations.RunSQL(
            "CREATE INDEX IF NOT EXISTS idx_trials_covering ON trials (trial_id, title, published_date, discovery_date);",
            reverse_sql="DROP INDEX IF EXISTS idx_trials_covering;"
        ),
    ]
