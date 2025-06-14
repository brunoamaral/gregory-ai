import re
import json
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta
from gregory.models import Articles, Trials, ArticleTrialReference


class Command(BaseCommand):
    help = 'Detects trial identifiers in article summaries and creates ArticleTrialReference objects'

    def add_arguments(self, parser):
        parser.add_argument(
            '--article-id',
            type=int,
            help='Process a specific article (by ID)'
        )
        parser.add_argument(
            '--trial-id',
            type=int,
            help='Process articles looking for a specific trial (by ID)'
        )
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Remove all existing article-trial references before scanning'
        )
        parser.add_argument(
            '--limit',
            type=int,
            help='Limit the number of articles processed'
        )
        parser.add_argument(
            '--recent',
            action='store_true',
            help='Process only articles from the last 30 days (or days specified by --days)'
        )
        parser.add_argument(
            '--days',
            type=int,
            default=30,
            help='Number of days to look back when using --recent (default: 30)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be created but don\'t save to database'
        )

    def handle(self, *args, **options):
        # Handle optional reset of all references
        if options['reset']:
            if options['dry_run']:
                count = ArticleTrialReference.objects.count()
                self.stdout.write(f"Would delete {count} existing references (dry run)")
            else:
                count = ArticleTrialReference.objects.count()
                ArticleTrialReference.objects.all().delete()
                self.stdout.write(f"Deleted {count} existing references")
        
        # Setup article query
        if options['article_id']:
            articles = Articles.objects.filter(article_id=options['article_id'])
            self.stdout.write(f"Processing specific article ID: {options['article_id']}")
        else:
            articles = Articles.objects.filter(summary__isnull=False).exclude(summary='')
            
            # Filter for recent articles if the option is specified
            if options['recent']:
                days = options['days']
                date_threshold = timezone.now() - timedelta(days=days)
                articles = articles.filter(discovery_date__gte=date_threshold)
                self.stdout.write(f"Processing articles from the last {days} days")
            else:
                self.stdout.write(f"Processing all articles with summaries")
        
        # Apply limit if specified
        if options['limit']:
            articles = articles[:options['limit']]
            self.stdout.write(f"Limiting to {options['limit']} articles")
        
        # Setup trial query
        if options['trial_id']:
            trials = Trials.objects.filter(trial_id=options['trial_id'])
            self.stdout.write(f"Looking for specific trial ID: {options['trial_id']}")
        else:
            trials = Trials.objects.filter(identifiers__isnull=False).exclude(identifiers={})
            self.stdout.write(f"Scanning for all trials with identifiers")
        
        self.stdout.write(f"Found {articles.count()} articles and {trials.count()} trials to process")
        
        # Track statistics
        total_references = 0
        total_articles_with_refs = set()
        total_trials_with_refs = set()
        
        # Process articles and trials
        for article in articles:
            if not article.summary:
                continue
            
            article_refs_count = 0
            
            for trial in trials:
                if not trial.identifiers:
                    continue
                
                # Check each identifier in the trial
                for id_type, id_value in trial.identifiers.items():
                    if not id_value:
                        continue
                    
                    # Skip empty or null values
                    id_value = str(id_value).strip()
                    if not id_value:
                        continue
                    
                    # Case-insensitive search in summary
                    if id_value.lower() in article.summary.lower():
                        total_references += 1
                        article_refs_count += 1
                        total_articles_with_refs.add(article.article_id)
                        total_trials_with_refs.add(trial.trial_id)
                        
                        if options['dry_run']:
                            self.stdout.write(f"Would create: Article {article.article_id} -> Trial {trial.trial_id} via {id_type}={id_value} (dry run)")
                        else:
                            # Create the reference object if it doesn't exist
                            ArticleTrialReference.objects.get_or_create(
                                article=article,
                                trial=trial,
                                identifier_type=id_type,
                                identifier_value=id_value
                            )
            
            # Print progress for articles with references
            if article_refs_count > 0:
                self.stdout.write(f"Article {article.article_id}: Found {article_refs_count} trial references")
        
        # Print summary
        action = "Would have created" if options['dry_run'] else "Created"
        self.stdout.write(self.style.SUCCESS(
            f"{action} {total_references} article-trial references "
            f"involving {len(total_articles_with_refs)} articles and {len(total_trials_with_refs)} trials"
        ))
