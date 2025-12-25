"""
Django management command to run ML predictions on newly-discovered articles.

This command classifies articles for every team/subject that opted-in to automatic prediction,
stores the results in MLPredictions, and logs each (subject × algorithm) run in PredictionRunLog.
"""
import os
import sys
import json
import traceback
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q, Exists, OuterRef
from django.utils import timezone

from gregory.models import Team, Articles, MLPredictions, PredictionRunLog
from gregory.utils.text_utils import cleanHTML, cleanText

# Base path for models
BASE_MODEL_DIR = os.path.join(settings.BASE_DIR, "models")

# Default settings
DEFAULT_LOOKBACK_DAYS = 90
DEFAULT_THRESHOLD = 0.8
DEFAULT_ALGORITHMS = ["pubmed_bert", "lgbm_tfidf", "lstm"]
DEFAULT_VERBOSITY = 1


class ModelLoadError(Exception):
    """Exception raised when a model cannot be loaded."""
    pass


def get_articles(subject, algorithm, model_version, lookback_days=None, all_articles=False):
    """
    Get articles that need prediction for a given subject, algorithm and model version.
    
    Args:
        subject (Subject): The subject to filter articles by
        algorithm (str): The algorithm to use for prediction
        model_version (str): The model version to use
        lookback_days (int, optional): Only include articles from the last N days
        all_articles (bool): If True, ignore the lookback_days filter
        
    Returns:
        QuerySet: Filtered articles queryset
    """
    # Find articles that already have predictions for this combination
    existing_predictions = MLPredictions.objects.filter(
        subject=subject,
        article=OuterRef('pk'),
        algorithm=algorithm,
        model_version=model_version
    )
    
    # Start with base queryset
    articles = Articles.objects.filter(subjects=subject)
    
    # Apply date filter if not all_articles
    if not all_articles and lookback_days:
        # Calculate the date threshold
        today = timezone.now().date()
        date_threshold = today - timedelta(days=lookback_days)
        articles = articles.filter(discovery_date__date__gte=date_threshold)
    
    # Add remaining filters
    articles = articles.annotate(
        has_prediction=Exists(existing_predictions)
    ).filter(
        has_prediction=False
    ).exclude(
        Q(summary__isnull=True) | Q(summary='')
    ).distinct()
    
    return articles


def resolve_model_version(base_path, explicit_version=None):
    """
    Resolve the model version to use.
    
    Args:
        base_path (str): Base path to the model directory
        explicit_version (str, optional): Explicitly requested version
        
    Returns:
        str: The resolved model version
        
    Raises:
        FileNotFoundError: If the model directory doesn't exist or has no versions
        ValueError: If the explicitly requested version doesn't exist
    """
    path = Path(base_path)
    
    # Check if the base directory exists
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"Model directory '{base_path}' not found")
    
    # Get all subdirectories (versions)
    versions = [d.name for d in path.iterdir() if d.is_dir()]
    
    if not versions:
        raise FileNotFoundError(f"No model versions found in '{base_path}'")
    
    # If explicit version is provided, check if it exists
    if explicit_version:
        if explicit_version in versions:
            return explicit_version
        else:
            raise FileNotFoundError(f"Requested model version '{explicit_version}' not found in '{base_path}'")
    
    # Otherwise, return the lexicographically largest (most recent) version
    return sorted(versions)[-1]


def load_model(team, subject, algorithm, model_version):
    """
    Load the appropriate model for a given team, subject, algorithm and version.
    
    Args:
        team (Team): The team 
        subject (Subject): The subject
        algorithm (str): The algorithm name ('pubmed_bert', 'lgbm_tfidf', or 'lstm')
        model_version (str): The model version to load
        
    Returns:
        object: The loaded model, ready for predictions
        
    Raises:
        ModelLoadError: If the model files cannot be found or loaded
    """
    # Import the necessary modules
    try:
        if algorithm == 'pubmed_bert':
            from gregory.ml.bert_wrapper import BertTrainer
        elif algorithm == 'lgbm_tfidf':
            from gregory.ml.lgbm_wrapper import LGBMTfidfTrainer
            import joblib
        elif algorithm == 'lstm':
            from gregory.ml.lstm_wrapper import LSTMTrainer
            import tensorflow as tf
            from tensorflow.keras.layers import TextVectorization
        else:
            raise ModelLoadError(f"Unsupported algorithm: {algorithm}")
    except ImportError as e:
        raise ModelLoadError(f"Failed to import required modules for {algorithm}: {str(e)}")
    
    # Construct the path to the model directory
    model_dir = os.path.join(
        BASE_MODEL_DIR,
        team.slug,
        subject.subject_slug,
        algorithm,
        model_version
    )
    
    if not os.path.exists(model_dir):
        raise ModelLoadError(f"Model directory not found: {model_dir}")
    
    try:
        # Load the appropriate model based on algorithm
        if algorithm == 'pubmed_bert':
            # For BERT, load weights and tokenizer
            model_path = os.path.join(model_dir, 'bert_weights.h5')
            if not os.path.exists(model_path):
                raise ModelLoadError(f"BERT weights not found at {model_path}")
            
            # Initialize and load the model
            model = BertTrainer()
            model.load_weights(model_path)
            return model
            
        elif algorithm == 'lgbm_tfidf':
            # For LGBM, load vectorizer and classifier
            vectorizer_path = os.path.join(model_dir, 'tfidf_vectorizer.joblib')
            # Try both possible classifier filenames
            model_path = os.path.join(model_dir, 'lgbm_classifier.joblib')  # Updated filename
            if not os.path.exists(model_path):
                # Fall back to alternate filename if first one doesn't exist
                model_path = os.path.join(model_dir, 'classifier.joblib')
                if not os.path.exists(model_path):
                    raise ModelLoadError(f"LGBM classifier not found at either:\n- {os.path.join(model_dir, 'lgbm_classifier.joblib')}\n- {os.path.join(model_dir, 'classifier.joblib')}")
                
            if not os.path.exists(vectorizer_path):
                raise ModelLoadError(f"TF-IDF vectorizer not found at {vectorizer_path}")
                
            # Load the model
            model = LGBMTfidfTrainer()
            model.vectorizer = joblib.load(vectorizer_path)
            model.model = joblib.load(model_path)
            return model
            
        elif algorithm == 'lstm':
            # For LSTM, load model and tokenizer
            model_path = os.path.join(model_dir, 'lstm_weights.h5')
            tokenizer_path = os.path.join(model_dir, 'tokenizer.json')
            
            if not os.path.exists(model_path):
                raise ModelLoadError(f"LSTM weights not found at {model_path}")
            if not os.path.exists(tokenizer_path):
                raise ModelLoadError(f"LSTM tokenizer not found at {tokenizer_path}")
                
            # Load the model
            model = LSTMTrainer()
            
            # Load the vectorizer config from JSON file first
            with open(tokenizer_path, 'r') as f:
                config = json.load(f)
            
            # Create new vectorizer with our standard settings
            model.vectorizer = TextVectorization(
                max_tokens=model.max_tokens,
                output_sequence_length=model.sequence_length,
                standardize=model._custom_standardization
            )
            
            # Set the vocabulary
            model.vectorizer.set_vocabulary(config['vocabulary'])
            
            # AFTER setting the vocabulary, create the model
            # This ensures the embedding layer has the right input_dim
            model.model = model._create_model()
            
            # Then load the weights
            model.model.load_weights(model_path)
            
            return model
    except Exception as e:
        raise ModelLoadError(f"Failed to load {algorithm} model: {str(e)}")


def prepare_text(article):
    """
    Prepare article text for prediction by concatenating title and summary and cleaning.
    
    Args:
        article (Articles): The article to prepare text for
        
    Returns:
        str: The prepared text
    """
    if article.summary:
        text = f"{article.title} {article.summary}"
    else:
        text = article.title
        
    # Clean the text
    return cleanText(cleanHTML(text))


class Command(BaseCommand):
    help = 'Run ML predictions on newly-discovered articles'

    def add_arguments(self, parser):
        # Scope arguments - must provide either --team or --all-teams
        scope_group = parser.add_argument_group('Scope')
        scope_group.add_argument(
            '--team', 
            type=str,
            help='Limit scope to one team (by slug)'
        )
        scope_group.add_argument(
            '--subject', 
            type=str,
            help='Limit scope to one subject (by slug, requires --team)'
        )
        scope_group.add_argument(
            '--all-teams', 
            action='store_true',
            help='Process all teams (ignores --team and --subject)'
        )
        
        # Filter arguments
        filter_group = parser.add_argument_group('Filters')
        filter_group.add_argument(
            '--lookback-days', 
            type=int, 
            default=DEFAULT_LOOKBACK_DAYS,
            help=f'Select articles whose discovery_date >= today - N days (default: {DEFAULT_LOOKBACK_DAYS})'
        )
        filter_group.add_argument(
            '--all-articles',
            action='store_true',
            help='Process all articles regardless of discovery date'
        )
        filter_group.add_argument(
            '--algo', 
            type=str,
            help='Comma-separated list of algorithms to use (default: all available)'
        )
        filter_group.add_argument(
            '--model-version', 
            type=str,
            help='Force a specific model version (default: latest available)'
        )
        filter_group.add_argument(
            '--prob-threshold', 
            type=float,
            default=DEFAULT_THRESHOLD,
            help=f'Probability threshold for positive predictions (default: {DEFAULT_THRESHOLD})'
        )
        
        # Output control arguments
        output_group = parser.add_argument_group('Output')
        output_group.add_argument(
            '--verbose', 
            type=int,
            default=DEFAULT_VERBOSITY,
            choices=[0, 1, 2, 3],
            help=f'Verbosity level (0-3, default: {DEFAULT_VERBOSITY})'
        )
        output_group.add_argument(
            '--dry-run', 
            action='store_true',
            help='Run everything except database writes'
        )

    def run_predictions_for(self, subject, algorithm, model_version, lookback_days=None, all_articles=False, prob_threshold=0.8, dry_run=False, verbose=1):
        """
        Run predictions for a specific subject and algorithm.
        
        Args:
            subject (Subject): The subject to run predictions for
            algorithm (str): The algorithm to use
            model_version (str): The model version to use (or None for latest)
            lookback_days (int, optional): Only include articles from the last N days
            all_articles (bool): If True, ignore the lookback_days filter
            prob_threshold (float): Probability threshold for positive class
            dry_run (bool): If True, don't write to the database
            verbose (int): Verbosity level (0-3)
            
        Returns:
            dict: Statistics about the run
        """
        stats = {
            'processed': 0,
            'skipped': 0,
            'failures': 0,
            'new_predictions': 0
        }
        
        # Create a PredictionRunLog entry
        if verbose >= 1:
            self.stdout.write(f"    Starting prediction run for {subject.subject_slug} using {algorithm}")
        
        run_log = None
        failed_articles = []
        prediction_instances = []
        
        try:
            # Create the run log entry (if not dry run)
            if not dry_run:
                run_log = PredictionRunLog.objects.create(
                    team=subject.team,
                    subject=subject,
                    algorithm=algorithm,
                    run_type='predict',
                    success=None,  # Will be updated at the end
                    triggered_by='predict_articles command'
                    # model_version will be added after resolution
                )
            
            # Resolve the model version if not explicitly provided
            try:
                base_path = os.path.join(BASE_MODEL_DIR, subject.team.slug, subject.subject_slug, algorithm)
                resolved_version = resolve_model_version(base_path, model_version)
                
                # Update the model_version in the run_log
                if run_log and not dry_run:
                    run_log.model_version = resolved_version
                    run_log.save()
                
                if verbose >= 1:
                    version_msg = "latest" if not model_version else model_version
                    self.stdout.write(f"    Using model version: {resolved_version} ({version_msg})")
            except (FileNotFoundError, ValueError) as e:
                if run_log and not dry_run:
                    run_log.success = False
                    run_log.error_message = f"Failed to resolve model version: {str(e)}"
                    run_log.run_finished = timezone.now()
                    run_log.save()
                raise ModelLoadError(f"Failed to resolve model version: {str(e)}")
                
            # Load the model
            try:
                model = load_model(subject.team, subject, algorithm, resolved_version)
                if verbose >= 2:
                    self.stdout.write(f"    Successfully loaded {algorithm} model")
            except ModelLoadError as e:
                if run_log and not dry_run:
                    run_log.success = False
                    run_log.error_message = f"Failed to load model: {str(e)}"
                    run_log.run_finished = timezone.now()
                    run_log.save()
                raise
            
            # Get articles that need prediction
            articles = get_articles(subject, algorithm, resolved_version, lookback_days, all_articles)
            # Handle both QuerySets and lists
            total_articles = len(articles) if isinstance(articles, list) else articles.count()
            
            if verbose >= 1:
                if all_articles:
                    self.stdout.write(f"    Found {total_articles} articles to process (all articles)")
                else:
                    self.stdout.write(f"    Found {total_articles} articles to process (last {lookback_days} days)")
            
            if total_articles == 0:
                # No articles to process, just update the log and return
                if run_log and not dry_run:
                    run_log.success = True
                    run_log.run_finished = timezone.now()
                    run_log.save()
                
                if verbose >= 2:
                    self.stdout.write(self.style.WARNING(f"    No articles found to process for {subject.subject_slug}/{algorithm}"))
                
                return stats
            
            # Process each article
            for article in articles:
                try:
                    # Prepare text
                    text = prepare_text(article)
                    
                    if not text:
                        stats['skipped'] += 1
                        if verbose >= 3:
                            self.stdout.write(f"    Skipped article {article.article_id}: No text after cleaning")
                        continue
                    
                    # Use the appropriate prediction method based on algorithm
                    if algorithm == 'pubmed_bert' or algorithm == 'lstm':
                        binary_prediction, probability = model.predict([text], threshold=prob_threshold)
                        # Handle both list return and single value return (for tests)
                        if isinstance(binary_prediction, list) and len(binary_prediction) > 0:
                            binary_prediction = binary_prediction[0]  # Extract single value from list
                        if isinstance(probability, list) and len(probability) > 0:
                            probability = probability[0]  # Extract single value from list
                    elif algorithm == 'lgbm_tfidf':
                        binary_prediction, probability = model.predict([text], threshold=prob_threshold)
                        # Handle both list return and single value return (for tests)
                        if isinstance(binary_prediction, list) and len(binary_prediction) > 0:
                            binary_prediction = binary_prediction[0]  # Extract single value from list
                        if isinstance(probability, list) and len(probability) > 0:
                            probability = probability[0]  # Extract single value from list
                    else:
                        raise ValueError(f"Unsupported algorithm: {algorithm}")
                    
                    # Create MLPredictions instance
                    prediction = MLPredictions(
                        subject=subject,
                        article=article,
                        model_version=resolved_version,  # Always use the resolved model version
                        algorithm=algorithm,
                        probability_score=probability,
                        predicted_relevant=(binary_prediction == 1)
                    )
                    
                    prediction_instances.append(prediction)
                    stats['processed'] += 1
                    
                    if verbose >= 3:
                        relevance = "relevant" if binary_prediction == 1 else "not relevant"
                        self.stdout.write(f"    Article {article.article_id}: {relevance} ({probability:.4f})")
                    
                except Exception as e:
                    stats['failures'] += 1
                    failed_articles.append(article.article_id)
                    if verbose >= 2:
                        self.stderr.write(self.style.ERROR(f"    Failed to process article {article.article_id}: {str(e)}"))
            
            # Bulk create MLPredictions (ignore conflicts in case of duplicates)
            if prediction_instances and not dry_run:
                created = len(MLPredictions.objects.bulk_create(
                    prediction_instances,
                    ignore_conflicts=True
                ))
                stats['new_predictions'] = created
            elif prediction_instances:
                # For dry run, we just count would-be creations
                stats['new_predictions'] = len(prediction_instances)
            
            # Update the PredictionRunLog
            if run_log and not dry_run:
                run_log.success = (stats['failures'] == 0)
                run_log.run_finished = timezone.now()
                if failed_articles:
                    run_log.error_message = f"Failed to process articles: {', '.join(map(str, failed_articles))}"
                run_log.save()
            
            if verbose >= 1:
                self.stdout.write(self.style.SUCCESS(
                    f"    Finished {subject.subject_slug}/{algorithm}: "
                    f"processed={stats['processed']}, "
                    f"skipped={stats['skipped']}, "
                    f"failures={stats['failures']}, "
                    f"new_predictions={stats['new_predictions']}"
                ))
            
            return stats
            
        except Exception as e:
            # Handle any unexpected errors
            if run_log and not dry_run:
                run_log.success = False
                run_log.error_message = f"Unexpected error: {str(e)}"
                run_log.run_finished = timezone.now()
                run_log.save()
            
            if verbose >= 1:
                self.stderr.write(self.style.ERROR(f"    Run failed: {str(e)}"))
            
            raise

    def handle(self, *args, **options):
        # Parse and validate arguments
        verbose = options['verbose']
        team_slug = options.get('team')
        subject_slug = options.get('subject')
        all_teams = options.get('all_teams')
        
        # Validate mutual exclusion: either --team or --all-teams must be provided
        if not team_slug and not all_teams:
            self.print_help('manage.py', 'predict_articles')
            self.stderr.write(self.style.ERROR('Error: Either --team or --all-teams must be provided'))
            sys.exit(1)
            
        # Validate: --subject requires --team
        if subject_slug and not team_slug:
            self.print_help('manage.py', 'predict_articles')
            self.stderr.write(self.style.ERROR('Error: --subject requires --team'))
            sys.exit(1)
            
        # Parse algorithms
        if options['algo']:
            algorithms = options['algo'].split(',')
            # Validate algorithm names
            for algo in algorithms:
                if algo not in DEFAULT_ALGORITHMS:
                    self.stderr.write(self.style.ERROR(f"Error: Unknown algorithm '{algo}', valid options are: {', '.join(DEFAULT_ALGORITHMS)}"))
                    sys.exit(1)
        else:
            algorithms = DEFAULT_ALGORITHMS
            
        # Get the teams to process
        if all_teams:
            teams = Team.objects.all()
            if verbose >= 1:
                self.stdout.write(f"Processing all teams")
        else:
            teams = Team.objects.filter(slug=team_slug)
            if not teams.exists():
                raise CommandError(f"Team '{team_slug}' not found")
            if verbose >= 1:
                self.stdout.write(f"Processing team '{team_slug}'")
                
        # Initialize statistics collection
        all_stats = []
        
        # Process each team
        for team in teams:
            # Get subjects for this team with auto_predict=True
            subjects = team.subjects.filter(auto_predict=True)
            
            # Apply subject filter if provided
            if subject_slug and not all_teams:
                subjects = subjects.filter(subject_slug=subject_slug)
                
            if not subjects.exists():
                if all_teams:
                    if verbose >= 2:
                        self.stdout.write(self.style.WARNING(f"No subjects with auto_predict=True found for team '{team.slug}', skipping"))
                    continue
                else:
                    raise CommandError(f"No subjects with auto_predict=True found for team '{team.slug}'")
            
            # Process each subject
            for subject in subjects:
                if verbose >= 1:
                    self.stdout.write(f"Processing subject '{subject.subject_slug}' for team '{team.slug}'")
                
                # Process each algorithm
                for algorithm in algorithms:
                    if verbose >= 1:
                        self.stdout.write(f"  Using algorithm '{algorithm}'")
                    
                    # Run predictions for this combination of subject and algorithm
                    try:
                        stats = self.run_predictions_for(
                            subject=subject, 
                            algorithm=algorithm,
                            model_version=options.get('model_version'),
                            lookback_days=options.get('lookback_days'),
                            all_articles=options.get('all_articles', False),
                            prob_threshold=options.get('prob_threshold'),
                            dry_run=options.get('dry_run', False),
                            verbose=verbose
                        )
                        
                        # Collect statistics for summary
                        stats_entry = {
                            'team': team.slug,
                            'subject': subject.subject_slug,
                            'algorithm': algorithm,
                            'processed': stats['processed'],
                            'skipped': stats['skipped'],
                            'new_predictions': stats['new_predictions'],
                            'failures': stats['failures'],
                            'success': stats['failures'] == 0
                        }
                        all_stats.append(stats_entry)
                        
                    except Exception as e:
                        self.stderr.write(self.style.ERROR(f"Error processing {subject.subject_slug}/{algorithm}: {str(e)}"))
                        if verbose >= 2:
                            self.stderr.write(traceback.format_exc())
                        
                        # Add failed run to statistics
                        all_stats.append({
                            'team': team.slug,
                            'subject': subject.subject_slug,
                            'algorithm': algorithm,
                            'processed': 0,
                            'skipped': 0,
                            'new_predictions': 0,
                            'failures': 1,
                            'success': False
                        })
        
        # Print summary table if verbose level is high enough
        if verbose >= 3 and all_stats:
            self.stdout.write("\nSummary:")
            self.stdout.write("-" * 80)
            
            # Print header
            header_fmt = "| {:<15} | {:<15} | {:<12} | {:>10} | {:>8} | {:>15} | {:>10} |"
            self.stdout.write(header_fmt.format(
                "Team", "Subject", "Algorithm", "Processed", "Skipped", "New Predictions", "Failures"
            ))
            self.stdout.write("-" * 80)
            
            # Print rows
            row_fmt = "| {:<15} | {:<15} | {:<12} | {:>10} | {:>8} | {:>15} | {:>10} |"
            for entry in all_stats:
                status = self.style.SUCCESS("✓") if entry['success'] else self.style.ERROR("✗")
                self.stdout.write(row_fmt.format(
                    entry['team'],
                    entry['subject'],
                    entry['algorithm'],
                    entry['processed'],
                    entry['skipped'],
                    entry['new_predictions'],
                    entry['failures']
                ))
            
            self.stdout.write("-" * 80)
            
            # Summary totals
            total_processed = sum(e['processed'] for e in all_stats)
            total_skipped = sum(e['skipped'] for e in all_stats)
            total_new = sum(e['new_predictions'] for e in all_stats)
            total_failures = sum(e['failures'] for e in all_stats)
            
            self.stdout.write(row_fmt.format(
                "TOTAL", "", "", 
                total_processed, total_skipped, total_new, total_failures
            ))
        
        # Print dry run notice if applicable
        if options.get('dry_run', False):
            self.stdout.write(self.style.WARNING("\nDRY RUN: No database changes were made"))
            
        # Always exit with code 0 as per spec
        sys.exit(0)
