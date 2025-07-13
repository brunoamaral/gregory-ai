from django.core.management.base import BaseCommand
from django.db.models import Q, F
from django.utils import timezone
from gregory.models import Articles, Trials, TeamCategory
import re
from datetime import timedelta
import logging

# Configure logging
logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Rebuilds category associations for articles and trials with improved efficiency and accuracy.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, help='Only process content from the last N days')
        parser.add_argument('--batch-size', type=int, default=1000, help='Batch size for processing')
        parser.add_argument('--min-score', type=int, default=3, help='Minimum score to categorize content')
        parser.add_argument('--articles-only', action='store_true', help='Only rebuild article categories')
        parser.add_argument('--trials-only', action='store_true', help='Only rebuild trial categories')
        parser.add_argument('--dry-run', action='store_true', help='Run without making changes, just report what would happen')
        parser.add_argument('--verbose', action='store_true', help='Show detailed progress information')

    def handle(self, *args, **options):
        self.dry_run = options.get('dry_run', False)
        self.verbose = options.get('verbose', False)
        days = options.get('days')
        batch_size = options.get('batch_size')
        min_score = options.get('min_score')
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE: No changes will be made to the database"))
        
        if not options.get('trials_only'):
            self.rebuild_cats_articles(days, batch_size, min_score)
        
        if not options.get('articles_only'):
            self.rebuild_cats_trials(days, batch_size, min_score)
            
        self.stdout.write(self.style.SUCCESS('Successfully rebuilt category associations.'))

    def log_message(self, message, level=1):
        """Log a message if verbosity is high enough"""
        if self.verbose or level == 0:
            self.stdout.write(message)
            logger.info(message)

    def rebuild_cats_articles(self, days=None, batch_size=1000, min_score=3):
        self.stdout.write("Processing articles categorization...")
        
        # Define date cutoff for incremental updates
        cutoff_date = None
        if days:
            cutoff_date = timezone.now() - timedelta(days=days)
            self.stdout.write(f"Processing articles updated since {cutoff_date}")
        
        # Clear existing relationships (all or just recent)
        if not self.dry_run:
            if days:
                # Only delete categorizations for recently updated articles
                recent_article_ids = Articles.objects.filter(
                    Q(discovery_date__gte=cutoff_date) | 
                    Q(last_updated__gte=cutoff_date)
                ).values_list('article_id', flat=True)
                
                count = Articles.team_categories.through.objects.filter(
                    articles_id__in=recent_article_ids
                ).count()
                
                Articles.team_categories.through.objects.filter(
                    articles_id__in=recent_article_ids
                ).delete()
                self.stdout.write(f"Cleared {count} category associations for {len(recent_article_ids)} recent articles")
            else:
                # Full rebuild
                count = Articles.team_categories.through.objects.count()
                Articles.team_categories.through.objects.all().delete()
                self.stdout.write(f"Cleared all {count} article category associations")
        
        # Process categories in batches
        categories = TeamCategory.objects.prefetch_related('subjects').all()
        total_categories = categories.count()
        total_added = 0
        
        for index, cat in enumerate(categories, 1):
            terms = cat.category_terms
            if not terms:
                self.log_message(f"Skipping category '{cat.category_name}' with no terms")
                continue
                
            self.stdout.write(f"[{index}/{total_categories}] Processing category: {cat.category_name}")
            
            # Prepare term patterns for more accurate matching
            term_patterns = [re.compile(r'\b' + re.escape(term.lower()) + r'\b') for term in terms]
            
            category_added = 0
            
            for subject in cat.subjects.all():
                self.log_message(f"  - Processing subject: {subject.subject_name}")
                
                total_articles = 0
                added_articles = 0
                offset = 0
                
                # Base query for this subject
                base_query = Articles.objects.filter(subjects__id=subject.id)
                
                # Apply date filter if incremental
                if cutoff_date:
                    base_query = base_query.filter(
                        Q(discovery_date__gte=cutoff_date) | 
                        Q(last_updated__gte=cutoff_date)
                    )
                
                # Initial database filtering (broad match)
                query = Q()
                for term in terms:
                    upper_term = term.upper()
                    query |= Q(utitle__contains=upper_term) | Q(usummary__contains=upper_term)
                
                candidates = base_query.filter(query)
                total_candidates = candidates.count()
                self.log_message(f"    Found {total_candidates} candidate articles")
                
                # Process in batches
                while True:
                    batch = list(candidates[offset:offset+batch_size])
                    if not batch:
                        break
                        
                    total_articles += len(batch)
                    
                    # Score-based categorization for this batch
                    articles_to_add = []
                    articles_with_scores = []
                    
                    for article in batch:
                        score = 0
                        matched_terms = set()
                        title = article.title.lower()
                        summary = (article.summary or "").lower()
                        
                        # Check for whole-word matches in title (higher weight)
                        for i, pattern in enumerate(term_patterns):
                            if pattern.search(title):
                                score += 3
                                matched_terms.add(terms[i])
                        
                        # Check for whole-word matches in summary
                        for i, pattern in enumerate(term_patterns):
                            if pattern.search(summary):
                                score += 1
                                matched_terms.add(terms[i])
                        
                        # Bonus for multiple term matches
                        score += len(matched_terms) * 2
                        
                        # Add if score meets threshold
                        if score >= min_score:
                            articles_to_add.append(article.article_id)
                            articles_with_scores.append((article.article_id, article.title, score, list(matched_terms)))
                    
                    # Bulk add to category
                    if articles_to_add and not self.dry_run:
                        cat.articles.add(*articles_to_add)
                    
                    added_articles += len(articles_to_add)
                    
                    # Log detailed info for verbose mode
                    if self.verbose:
                        for article_id, title, score, matched in articles_with_scores:
                            self.log_message(f"      Article {article_id}: Score {score}, Terms: {', '.join(matched)}")
                            self.log_message(f"        Title: {title[:100]}...")
                    
                    offset += batch_size
                    self.log_message(f"    Processed {min(total_articles, total_candidates)} of {total_candidates} articles")
                
                category_added += added_articles
                self.stdout.write(f"    Added {added_articles} articles for subject '{subject.subject_name}'")
            
            total_added += category_added
            self.stdout.write(f"  Total added to '{cat.category_name}': {category_added} articles")
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING(f"DRY RUN: Would have added {total_added} article categorizations"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Added {total_added} article categorizations in total"))

    def rebuild_cats_trials(self, days=None, batch_size=1000, min_score=3):
        self.stdout.write("Processing trials categorization...")
        
        # Define date cutoff for incremental updates
        cutoff_date = None
        if days:
            cutoff_date = timezone.now() - timedelta(days=days)
            self.stdout.write(f"Processing trials updated since {cutoff_date}")
        
        # Clear existing relationships (all or just recent)
        if not self.dry_run:
            if days:
                # Only delete categorizations for recently updated trials
                recent_trial_ids = Trials.objects.filter(
                    Q(discovery_date__gte=cutoff_date) | 
                    Q(last_updated__gte=cutoff_date)
                ).values_list('trial_id', flat=True)
                
                count = Trials.team_categories.through.objects.filter(
                    trials_id__in=recent_trial_ids
                ).count()
                
                Trials.team_categories.through.objects.filter(
                    trials_id__in=recent_trial_ids
                ).delete()
                self.stdout.write(f"Cleared {count} category associations for {len(recent_trial_ids)} recent trials")
            else:
                # Full rebuild
                count = Trials.team_categories.through.objects.count()
                Trials.team_categories.through.objects.all().delete()
                self.stdout.write(f"Cleared all {count} trial category associations")
        
        # Process categories in batches
        categories = TeamCategory.objects.prefetch_related('subjects').all()
        total_categories = categories.count()
        total_added = 0
        
        for index, cat in enumerate(categories, 1):
            terms = cat.category_terms
            if not terms:
                self.log_message(f"Skipping category '{cat.category_name}' with no terms")
                continue
                
            self.stdout.write(f"[{index}/{total_categories}] Processing category: {cat.category_name}")
            
            # Prepare term patterns for more accurate matching
            term_patterns = [re.compile(r'\b' + re.escape(term.lower()) + r'\b') for term in terms]
            
            category_added = 0
            
            for subject in cat.subjects.all():
                self.log_message(f"  - Processing subject: {subject.subject_name}")
                
                total_trials = 0
                added_trials = 0
                offset = 0
                
                # Base query for this subject
                base_query = Trials.objects.filter(subjects__id=subject.id)
                
                # Apply date filter if incremental
                if cutoff_date:
                    base_query = base_query.filter(
                        Q(discovery_date__gte=cutoff_date) | 
                        Q(last_updated__gte=cutoff_date)
                    )
                
                # Initial database filtering (broad match)
                query = Q()
                for term in terms:
                    upper_term = term.upper()
                    query |= Q(utitle__contains=upper_term) | Q(usummary__contains=upper_term)
                
                candidates = base_query.filter(query)
                total_candidates = candidates.count()
                self.log_message(f"    Found {total_candidates} candidate trials")
                
                # Process in batches
                while True:
                    batch = list(candidates[offset:offset+batch_size])
                    if not batch:
                        break
                        
                    total_trials += len(batch)
                    
                    # Score-based categorization for this batch
                    trials_to_add = []
                    trials_with_scores = []
                    
                    for trial in batch:
                        score = 0
                        matched_terms = set()
                        title = trial.title.lower()
                        summary = (trial.summary or "").lower()
                        
                        # Check for whole-word matches in title (higher weight)
                        for i, pattern in enumerate(term_patterns):
                            if pattern.search(title):
                                score += 3
                                matched_terms.add(terms[i])
                        
                        # Check for whole-word matches in summary
                        for i, pattern in enumerate(term_patterns):
                            if pattern.search(summary):
                                score += 1
                                matched_terms.add(terms[i])
                        
                        # Bonus for multiple term matches
                        score += len(matched_terms) * 2
                        
                        # Add if score meets threshold
                        if score >= min_score:
                            trials_to_add.append(trial.trial_id)
                            trials_with_scores.append((trial.trial_id, trial.title, score, list(matched_terms)))
                    
                    # Bulk add to category
                    if trials_to_add and not self.dry_run:
                        cat.trials.add(*trials_to_add)
                    
                    added_trials += len(trials_to_add)
                    
                    # Log detailed info for verbose mode
                    if self.verbose:
                        for trial_id, title, score, matched in trials_with_scores:
                            self.log_message(f"      Trial {trial_id}: Score {score}, Terms: {', '.join(matched)}")
                            self.log_message(f"        Title: {title[:100]}...")
                    
                    offset += batch_size
                    self.log_message(f"    Processed {min(total_trials, total_candidates)} of {total_candidates} trials")
                
                category_added += added_trials
                self.stdout.write(f"    Added {added_trials} trials for subject '{subject.subject_name}'")
            
            total_added += category_added
            self.stdout.write(f"  Total added to '{cat.category_name}': {category_added} trials")
        
        if self.dry_run:
            self.stdout.write(self.style.WARNING(f"DRY RUN: Would have added {total_added} trial categorizations"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Added {total_added} trial categorizations in total"))