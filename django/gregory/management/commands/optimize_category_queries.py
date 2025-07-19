"""
Management command to apply database optimizations for category queries.

This command adds the necessary indexes to improve performance of category-related
queries that were causing database hanging issues.
"""

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Add database indexes to optimize category queries and prevent hanging'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what indexes would be created without actually creating them',
        )

    def handle(self, *args, **options):
        """Apply database optimizations for category queries."""
        
        optimizations = [
            # Add indexes for many-to-many relationship tables
            ("Many-to-many indexes", [
                "CREATE INDEX IF NOT EXISTS idx_articles_team_categories_category_id ON articles_team_categories (teamcategory_id);",
                "CREATE INDEX IF NOT EXISTS idx_articles_team_categories_article_id ON articles_team_categories (articles_id);",
                "CREATE INDEX IF NOT EXISTS idx_trials_team_categories_category_id ON trials_team_categories (teamcategory_id);",
                "CREATE INDEX IF NOT EXISTS idx_trials_team_categories_trial_id ON trials_team_categories (trials_id);",
                "CREATE INDEX IF NOT EXISTS idx_articles_authors_article_id ON articles_authors (articles_id);",
                "CREATE INDEX IF NOT EXISTS idx_articles_authors_author_id ON articles_authors (authors_id);",
            ]),
            
            # Add indexes for date filtering
            ("Date filtering indexes", [
                "CREATE INDEX IF NOT EXISTS idx_articles_published_date ON articles (published_date);",
                "CREATE INDEX IF NOT EXISTS idx_articles_discovery_date ON articles (discovery_date);",
                "CREATE INDEX IF NOT EXISTS idx_trials_published_date ON trials (published_date);",
                "CREATE INDEX IF NOT EXISTS idx_trials_discovery_date ON trials (discovery_date);",
            ]),
            
            # Add composite indexes for common query patterns
            ("Composite indexes", [
                "CREATE INDEX IF NOT EXISTS idx_team_categories_team_subject ON team_categories (team_id, id);",
                "CREATE INDEX IF NOT EXISTS idx_ml_predictions_article_algorithm ON ml_predictions (article_id, algorithm);",
                "CREATE INDEX IF NOT EXISTS idx_ml_predictions_score_threshold ON ml_predictions (probability_score) WHERE probability_score >= 0.5;",
            ]),
            
            # Add indexes for the team categories table
            ("Category table indexes", [
                "CREATE INDEX IF NOT EXISTS idx_team_categories_slug ON team_categories (category_slug);",
                "CREATE INDEX IF NOT EXISTS idx_team_categories_team_id ON team_categories (team_id);",
            ]),
            
            # Add covering indexes for frequently accessed fields
            ("Covering indexes", [
                "CREATE INDEX IF NOT EXISTS idx_articles_covering ON articles (article_id, title, published_date, discovery_date);",
                "CREATE INDEX IF NOT EXISTS idx_trials_covering ON trials (trial_id, title, published_date, discovery_date);",
            ])
        ]

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('DRY RUN: The following indexes would be created:'))
            for category, queries in optimizations:
                self.stdout.write(f"\n{self.style.SUCCESS(category)}:")
                for query in queries:
                    self.stdout.write(f"  {query}")
            return

        total_indexes = sum(len(queries) for _, queries in optimizations)
        self.stdout.write(f"Creating {total_indexes} database indexes to optimize category queries...")

        with connection.cursor() as cursor:
            created_count = 0
            failed_count = 0
            
            for category, queries in optimizations:
                self.stdout.write(f"\nCreating {category}...")
                
                for query in queries:
                    try:
                        self.stdout.write(f"  Creating index: {query[:60]}...")
                        cursor.execute(query)
                        created_count += 1
                        self.stdout.write(f"    {self.style.SUCCESS('✓ Created')}")
                    except Exception as e:
                        failed_count += 1
                        self.stdout.write(f"    {self.style.ERROR('✗ Failed')}: {str(e)}")

        self.stdout.write(f"\n{self.style.SUCCESS('Optimization complete!')}")
        self.stdout.write(f"Created: {created_count} indexes")
        if failed_count > 0:
            self.stdout.write(f"Failed: {failed_count} indexes")

        # Analyze tables to update statistics
        self.stdout.write("\nUpdating table statistics...")
        with connection.cursor() as cursor:
            tables_to_analyze = [
                'articles', 'trials', 'team_categories', 'authors',
                'articles_team_categories', 'trials_team_categories', 
                'articles_authors', 'ml_predictions'
            ]
            
            for table in tables_to_analyze:
                try:
                    cursor.execute(f"ANALYZE {table};")
                    self.stdout.write(f"  Analyzed {table}")
                except Exception as e:
                    self.stdout.write(f"  Failed to analyze {table}: {str(e)}")

        self.stdout.write(f"\n{self.style.SUCCESS('Database optimization completed successfully!')}")
        self.stdout.write("\nThe following queries should now be much faster:")
        self.stdout.write("  - Category listing with article/trial counts")
        self.stdout.write("  - Author statistics for categories")
        self.stdout.write("  - Monthly counts with ML predictions")
        self.stdout.write("  - Complex category filtering operations")
