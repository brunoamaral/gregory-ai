import argparse
from datetime import datetime
from enum import IntEnum
import json
import os
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import numpy as np
import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from gregory.models import Team, Subject, Articles, PredictionRunLog
from gregory.utils.dataset import collect_articles, build_dataset, train_val_test_split
from gregory.utils.summariser import summarise_bulk
from gregory.utils.metrics import evaluate_binary
from gregory.utils.versioning import make_version_path
from gregory.utils.verboser import Verboser, VerbosityLevel
# Import ML utilities - with fallback mechanism
try:
    from gregory.ml import get_trainer
    from gregory.ml.pseudo import generate_pseudo_labels, save_pseudo_csv
    ML_AVAILABLE = True
except Exception as e:
    import warnings
    warnings.warn(f"ML modules could not be imported: {str(e)}. Some features will be unavailable.")
    ML_AVAILABLE = False
    
    # Define stub functions for graceful degradation
    def get_trainer(*args, **kwargs):
        raise ImportError("ML modules could not be imported")
        
    def generate_pseudo_labels(*args, **kwargs):
        raise ImportError("ML modules could not be imported")
        
    def save_pseudo_csv(*args, **kwargs):
        raise ImportError("ML modules could not be imported")

# ML import status tracking (for debug mode)
_ml_import_status = {}

def _check_ml_imports(stdout=None):
    """
    Check ML imports and optionally print status.
    Called when --debug flag is used.
    """
    global _ml_import_status
    
    def _print(msg):
        if stdout:
            stdout.write(msg + "\n")
        else:
            print(msg)
    
    try:
        from gregory.ml.bert_wrapper import BertTrainer
        _ml_import_status['BertTrainer'] = True
        _print("✓ Successfully imported BertTrainer")
    except Exception as e:
        _ml_import_status['BertTrainer'] = str(e)
        _print(f"✗ Failed to import BertTrainer: {e}")

    try:
        from gregory.ml.lgbm_wrapper import LGBMTfidfTrainer
        _ml_import_status['LGBMTfidfTrainer'] = True
        _print("✓ Successfully imported LGBMTfidfTrainer")
    except Exception as e:
        _ml_import_status['LGBMTfidfTrainer'] = str(e)
        _print(f"✗ Failed to import LGBMTfidfTrainer: {e}")

    try:
        from gregory.ml.lstm_wrapper import LSTMTrainer
        _ml_import_status['LSTMTrainer'] = True
        _print("✓ Successfully imported LSTMTrainer")
    except Exception as e:
        _ml_import_status['LSTMTrainer'] = str(e)
        _print(f"✗ Failed to import LSTMTrainer: {e}")

    try:
        from gregory.ml import get_trainer
        _ml_import_status['get_trainer'] = True
        _print("✓ Successfully imported get_trainer")
    except Exception as e:
        _ml_import_status['get_trainer'] = str(e)
        _print(f"✗ Failed to import get_trainer: {e}")
    
    return _ml_import_status

class Command(BaseCommand):
    """
    Train machine learning models for Gregory AI.

    This management command trains classifiers for any combination of teams and subjects,
    writes versioned model artifacts to disk, and records a PredictionRunLog row for each
    training run.

    Usage:
        python manage.py train_models --team TEAM_SLUG [--subject SUBJECT_SLUG] [options]
        python manage.py train_models --all-teams [options]

    Examples:
        # Train all algorithms for a specific team and subject
        python manage.py train_models --team research --subject oncology

        # Train BERT only for all subjects in the 'clinical' team
        python manage.py train_models --team clinical --algo pubmed_bert

        # Train all models for all teams, with verbose output
        python manage.py train_models --all-teams --verbose 3

        # Run with pseudo-labeling and a custom probability threshold
        python manage.py train_models --team research --subject cardiology --pseudo-label --prob-threshold 0.75
    """
    help = "Train Gregory AI text classifiers for teams and subjects"
    
    def add_arguments(self, parser):
        scope_group = parser.add_mutually_exclusive_group(required=True)
        scope_group.add_argument(
            "--team",
            type=str, 
            help="Team slug to train models for",
        )
        scope_group.add_argument(
            "--all-teams",
            action="store_true",
            help="Train models for all teams",
        )
        
        parser.add_argument(
            "--subject",
            type=str,
            help="Subject slug within the chosen team (if not specified, train for all subjects)",
        )
        
        window_group = parser.add_mutually_exclusive_group()
        window_group.add_argument(
            "--all-articles", 
            action="store_true",
            help="Use all labeled articles (ignores 90-day window)",
        )
        window_group.add_argument(
            "--lookback-days",
            type=int,
            help="Override the default 90-day window for article discovery",
        )
        
        parser.add_argument(
            "--algo",
            type=str,
            default="pubmed_bert,lgbm_tfidf,lstm",
            help="Comma-separated list of algorithms to train (pubmed_bert,lgbm_tfidf,lstm)",
        )
        
        parser.add_argument(
            "--prob-threshold",
            type=float,
            default=0.8,
            help="Probability threshold for classification (default: 0.8)",
        )
        
        parser.add_argument(
            "--model-version",
            type=str,
            help="Manual version tag (default: auto-generated YYYYMMDD with optional _n suffix)",
        )
        
        parser.add_argument(
            "--pseudo-label",
            action="store_true",
            help="Run BERT self-training loop before final training",
        )
        
        parser.add_argument(
            "--verbose",
            type=int,
            choices=[0, 1, 2, 3],
            default=1,
            help="Verbosity level (0: quiet, 1: progress, 2: +warnings, 3: +summary)",
        )
        parser.add_argument(
            "--debug",
            action="store_true",
            help="Enable debug mode (prints ML import status and additional diagnostics)",
        )

    def validate_arguments(self, options):
        """
        Validate command arguments for consistency and correctness.
        
        Args:
            options: The parsed command arguments
            
        Raises:
            CommandError: If arguments are inconsistent or invalid
        """
        print(f"{options}")
        # Validate team and subject arguments
        if not options["all_teams"] and options["team"] is None:
            raise CommandError("Either --team or --all-teams must be specified")
            
        if options["subject"] and not options["team"]:
            raise CommandError("--subject can only be used with --team, not with --all-teams")
        
        # Validate articles selection arguments
        if options["all_articles"] and options["lookback_days"]:
            raise CommandError("--all-articles and --lookback-days are mutually exclusive")
        
        # Validate algorithms
        valid_algos = {"pubmed_bert", "lgbm_tfidf", "lstm"}
        input_algos = {algo.strip() for algo in options["algo"].split(",")}
        
        if not input_algos.issubset(valid_algos):
            invalid_algos = input_algos - valid_algos
            raise CommandError(f"Invalid algorithm(s): {', '.join(invalid_algos)}. "
                              f"Valid options are: {', '.join(valid_algos)}")
        
        # Validate probability threshold
        if not (0 < options["prob_threshold"] < 1):
            raise CommandError("--prob-threshold must be between 0 and 1")
        
        # Store parsed algorithms back in options
        options["parsed_algos"] = list(input_algos)
    
    def setup_verboser(self, verbosity: int):
        """
        Set up a Verboser instance with the appropriate verbosity level.
        
        Args:
            verbosity: The verbosity level from command arguments
        """
        self.verboser = Verboser(
            level=verbosity,
            stdout=self.stdout,
            stderr=self.stderr,
            use_styling=True  # Use ANSI styling for better readability
        )
    
    def log_message(self, message: str, min_verbosity: VerbosityLevel):
        """
        Log a message if verbosity level is sufficient.
        
        Args:
            message: The message to log
            min_verbosity: Minimum verbosity level required to show this message
        """
        self.verboser.info(message, min_level=min_verbosity)
    
    def log_success(self, message: str, min_verbosity: VerbosityLevel = VerbosityLevel.PROGRESS):
        """Log a success message with formatting if verbosity level is sufficient."""
        self.verboser.success(message, min_level=min_verbosity)
    
    def log_warning(self, message: str, min_verbosity: VerbosityLevel = VerbosityLevel.WARNINGS):
        """Log a warning message with formatting if verbosity level is sufficient."""
        self.verboser.warn(message, min_level=min_verbosity)
    
    def log_error(self, message: str, min_verbosity: VerbosityLevel = VerbosityLevel.PROGRESS):
        """Log an error message with formatting if verbosity level is sufficient."""
        self.verboser.error(message, min_level=min_verbosity)
    
    def get_teams_and_subjects(self, options) -> List[Tuple[str, List[str]]]:
        """
        Get the list of team slugs and their subject slugs based on command options.
        
        Args:
            options: The parsed command arguments
            
        Returns:
            List of (team_slug, [subject_slugs]) tuples
        """
        result = []
        
        if options["all_teams"]:
            # Fetch all teams and their subjects
            teams = Team.objects.all()
            for team in teams:
                subject_slugs = list(team.subjects.values_list('subject_slug', flat=True))
                if subject_slugs:  # Only include teams with at least one subject
                    result.append((team.slug, subject_slugs))
        else:
            # Fetch specific team and subject(s)
            try:
                team = Team.objects.get(slug=options["team"])
            except Team.DoesNotExist:
                raise CommandError(f"Team with slug '{options['team']}' does not exist")
            
            if options["subject"]:
                # Specific subject
                try:
                    subject = Subject.objects.get(team=team, subject_slug=options["subject"])
                    result.append((team.slug, [subject.subject_slug]))
                except Subject.DoesNotExist:
                    raise CommandError(
                        f"Subject with slug '{options['subject']}' does not exist in team '{team.slug}'"
                    )
            else:
                # All subjects for the team
                subject_slugs = list(team.subjects.values_list('subject_slug', flat=True))
                if subject_slugs:
                    result.append((team.slug, subject_slugs))
                else:
                    self.log_warning(f"Team '{team.slug}' has no subjects, skipping", VerbosityLevel.WARNINGS)
        
        return result
        
    def run_training_pipeline(
        self,
        team_slug: str,
        subject_slug: str,
        algorithm: str,
        options: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Run the complete training pipeline for a specific team, subject, and algorithm.
        
        Args:
            team_slug: The team slug
            subject_slug: The subject slug
            algorithm: The algorithm name
            options: Command options
            
        Returns:
            Dict with training results including metrics
            
        Raises:
            ValueError: If dataset preparation or training fails
            
        Note:
            Generated summaries are stored as 'generated_summary' in the training DataFrame
            and are never saved back to the database. They are only used for training purposes.
        """
        results = {
            'team': team_slug,
            'subject': subject_slug,
            'algorithm': algorithm,
            'success': False,
            'metrics': {}
        }
        
        # Get the base model directory path
        BASE_MODEL_DIR = os.path.join(settings.BASE_DIR, "models")
        
        # Create version path for this run
        model_version = options.get("model_version")
        if model_version:
            model_dir = Path(BASE_MODEL_DIR) / team_slug / subject_slug / algorithm / model_version
            model_dir.mkdir(parents=True, exist_ok=True)
        else:
            model_dir = make_version_path(BASE_MODEL_DIR, team_slug, subject_slug, algorithm)
        
        results['model_dir'] = str(model_dir)
        results['model_version'] = model_dir.name
        
        # Log start of training
        self.log_message(
            f"Starting training for {team_slug}/{subject_slug} using {algorithm}", 
            VerbosityLevel.PROGRESS
        )
        
        # Step 1: Collect and prepare data
        window_days = None if options["all_articles"] else options.get("lookback_days", 90)
        
        self.log_message(
            f"Collecting articles for {team_slug}/{subject_slug}", 
            VerbosityLevel.PROGRESS
        )
        
        # Collect articles query
        articles_qs = collect_articles(team_slug, subject_slug, window_days)
        
        # Add detailed logging about article counts
        total_articles = Articles.objects.filter(teams__slug=team_slug, subjects__subject_slug=subject_slug).count()
        labeled_articles = articles_qs.count()
        
        
        if options["all_articles"]:
            self.log_message(f"Found {labeled_articles} labeled articles out of {total_articles} total articles (using all-articles flag)", VerbosityLevel.PROGRESS)
        else:
            window_msg = f"last {window_days} days" if window_days else "all time"
            self.log_message(f"Found {labeled_articles} labeled articles out of {total_articles} total articles ({window_msg})", VerbosityLevel.PROGRESS)
        
        # Build dataset with collected articles
        self.log_message(f"Building dataset with {labeled_articles} labeled articles", VerbosityLevel.PROGRESS)
        dataset_df = build_dataset(articles_qs)
        
        if len(dataset_df) == 0:
            raise ValueError(f"No labeled articles found for {team_slug}/{subject_slug}")
        
        # Add class distribution check
        class_counts = dataset_df['relevant'].value_counts()
        self.log_message(
            f"Class distribution: relevant={class_counts.get(1, 0)}, "
            f"not relevant={class_counts.get(0, 0)}",
            VerbosityLevel.WARNINGS
        )

        # Check minimum samples per class
        min_class_count = class_counts.min() if not class_counts.empty else 0
        
        # If we have fewer than 2 samples in any class, we can't train properly
        if len(class_counts) < 2:
            raise ValueError(f"Dataset has only one class. Two classes (0 and 1) are required for training.")
            
        if min_class_count < 2:
            raise ValueError(f"The least populated class in y has only {min_class_count} member, which is too few. " +
                             "The minimum number of groups for any class cannot be less than 2.")
            
        if min_class_count < 3:
            self.log_warning(
                f"Dataset has only {min_class_count} samples in the smallest class. "
                f"Training might not be effective. Stratified splitting will be disabled.",
                VerbosityLevel.WARNINGS
            )
        
        self.log_message(
            f"Built dataset with {len(dataset_df)} labeled articles", 
            VerbosityLevel.PROGRESS
        )
        
        # Step 2: Generate summaries for all texts
        self.log_message("Generating text summaries...", VerbosityLevel.PROGRESS)
        
        # We already have the text combined from build_dataset, but we need to 
        # extract titles if we need to separately summarize
        if 'title' not in dataset_df.columns:
            # If we only have the combined text, we'll work with that directly
            titles = dataset_df['text'].tolist()
        else:
            titles = dataset_df['title'].tolist()
        
        # Generate summaries for all texts
        summaries = summarise_bulk(titles, batch_size=4, usage_type='training')
        
        # Update the dataset with generated summaries - use 'generated_summary' instead of 'summary'
        # to avoid overwriting original article abstracts
        dataset_df['generated_summary'] = summaries
        
        # Create final text column combining title and generated summary
        dataset_df['text'] = dataset_df.apply(
            lambda row: f"{row['title']} {row['generated_summary']}".strip() 
            if 'title' in dataset_df.columns 
            else f"{row['text']} {row['generated_summary']}".strip(),
            axis=1
        )
        
        self.log_message("Text summarization complete", VerbosityLevel.PROGRESS)
        
        # Double-check the class distribution before splitting
        class_counts = dataset_df['relevant'].value_counts()
        if len(class_counts) < 2:
            raise ValueError(f"Cannot split dataset: only one class present ({class_counts.to_dict()})")
            
        min_class_count = class_counts.min()
        if min_class_count < 2:
            raise ValueError(f"Cannot split dataset: the minority class has only {min_class_count} samples. " +
                             f"Need at least 2 examples per class. Class distribution: {class_counts.to_dict()}")
            
        # Step 3: Split the dataset
        try:
            self.log_message("Splitting dataset into train/val/test sets", VerbosityLevel.PROGRESS)
            self.log_message(f"Class distribution before split: {class_counts.to_dict()}", VerbosityLevel.WARNINGS)
            
            train_df, val_df, test_df = train_val_test_split(dataset_df)
            
            # Log class distribution in splits for debugging
            train_counts = train_df['relevant'].value_counts().to_dict()
            val_counts = val_df['relevant'].value_counts().to_dict()
            test_counts = test_df['relevant'].value_counts().to_dict()
            
            self.log_message(
                f"Split complete: train={len(train_df)} {train_counts}, " +
                f"val={len(val_df)} {val_counts}, test={len(test_df)} {test_counts}", 
                VerbosityLevel.PROGRESS
            )
        except ValueError as e:
            raise ValueError(f"Dataset splitting failed: {str(e)}")
        
        # Step 4: Apply pseudo-labeling if enabled
        if options["pseudo_label"]:
            self.log_message(
                "Applying pseudo-labeling with self-training", 
                VerbosityLevel.PROGRESS
            )
            
            # In a real implementation, we'd query for unlabeled articles
            # For now, we'll use a custom query to get articles without subject relevance
            
            # Query for articles that don't have relevance entries for this subject
            # This is a simplified version - in production, use a more efficient query
            self.log_message("Collecting unlabeled articles...", VerbosityLevel.PROGRESS)
            
            # Get articles from the same team but without relevance labels for this subject
            unlabeled_articles = Articles.objects.filter(
                teams__slug=team_slug
            ).exclude(
                article_subject_relevances__subject__slug=subject_slug
            )[:100]  # Limit to 100 for efficiency in this example
            
            # Convert to DataFrame with the same structure
            unlabeled_data = []
            for article in unlabeled_articles:
                unlabeled_data.append({
                    'article_id': article.article_id,
                    'title': article.title,
                    'summary': article.summary or "",
                    'text': f"{article.title} {article.summary or ''}".strip()
                })
            
            if not unlabeled_data:
                self.log_warning(
                    "No unlabeled articles found, skipping pseudo-labeling",
                    VerbosityLevel.WARNINGS
                )
            else:
                unlabelled_df = pd.DataFrame(unlabeled_data)
                
                # Generate summaries for unlabeled data if needed
                if len(unlabeled_data) > 0:
                    unlabeled_titles = unlabelled_df['title'].tolist()
                    unlabeled_summaries = summarise_bulk(unlabeled_titles, batch_size=4, usage_type='training')
                    unlabelled_df['generated_summary'] = unlabeled_summaries
                    unlabelled_df['text'] = unlabelled_df.apply(
                        lambda row: f"{row['title']} {row['generated_summary']}".strip(),
                        axis=1
                    )
                
                # Generate pseudo-labels
                self.log_message(
                    f"Starting pseudo-labeling with {len(unlabelled_df)} unlabeled examples",
                    VerbosityLevel.PROGRESS
                )
                enhanced_train_df = generate_pseudo_labels(
                    train_df=train_df,
                    val_df=val_df,
                    unlabelled_df=unlabelled_df,
                    confidence=0.9, 
                    max_iter=7,
                    algorithm=algorithm
                )
            
            # Save pseudo-labels to CSV
            pseudo_dir = Path(BASE_MODEL_DIR) / team_slug / subject_slug / "pseudo_labels"
            pseudo_file = save_pseudo_csv(enhanced_train_df, pseudo_dir, prefix=algorithm)
            
            self.log_message(
                f"Saved pseudo-labels to {pseudo_file}", 
                VerbosityLevel.PROGRESS
            )
            
            # Use the enhanced training set
            train_df = enhanced_train_df
        
        # Step 5: Train the model
        self.log_message(f"Initializing {algorithm} trainer", VerbosityLevel.PROGRESS)
        
        # Get appropriate trainer for the algorithm
        trainer = get_trainer(algorithm)
        
        # Define text column name
        text_column = 'text'
        
        # Prepare data for training
        train_texts = train_df[text_column].tolist()
        train_labels = train_df['relevant'].tolist() 
        val_texts = val_df[text_column].tolist()
        val_labels = val_df['relevant'].tolist()
        test_texts = test_df[text_column].tolist()
        test_labels = test_df['relevant'].tolist()
        
        # Train the model
        self.log_message(f"Training {algorithm} model", VerbosityLevel.PROGRESS)
        threshold = options["prob_threshold"]
        
        trainer.train(
            train_texts=train_texts, 
            train_labels=train_labels,
            val_texts=val_texts, 
            val_labels=val_labels,
            epochs=10,  # Use default or set based on algorithm
            batch_size=16  # Use default or set based on algorithm
        )
        
        # Step 6: Evaluate the model and get metrics
        self.log_message("Evaluating model performance", VerbosityLevel.PROGRESS)
        
        try:
            # Evaluate on validation set
            val_metrics = trainer.evaluate(
                test_texts=val_texts, 
                test_labels=val_labels,
                threshold=threshold
            )
            
            # Evaluate on test set
            test_metrics = trainer.evaluate(
                test_texts=test_texts, 
                test_labels=test_labels,
                threshold=threshold
            )
            
            # Format metrics with prefixes for consistency
            formatted_metrics = {}
            
            # Helper function to sanitize values for JSON serialization
            def sanitize_value(value):
                """Convert value to JSON-serializable type."""
                if isinstance(value, (int, float, str, bool, type(None))):
                    return value
                elif isinstance(value, (list, tuple)):
                    return [sanitize_value(v) for v in value]
                elif isinstance(value, dict):
                    return {k: sanitize_value(v) for k, v in value.items()}
                elif isinstance(value, np.ndarray):
                    return value.tolist()
                elif hasattr(value, '__float__'):
                    return float(value)
                elif hasattr(value, '__int__'):
                    return int(value)
                else:
                    # Skip non-serializable objects (like methods)
                    return None
            
            # Add validation metrics with 'val_' prefix
            for key, value in val_metrics.items():
                sanitized = sanitize_value(value)
                if sanitized is not None:  # Only include serializable values
                    formatted_metrics[f"val_{key}"] = sanitized
            
            # Add test metrics with 'test_' prefix
            for key, value in test_metrics.items():
                sanitized = sanitize_value(value)
                if sanitized is not None:  # Only include serializable values
                    formatted_metrics[f"test_{key}"] = sanitized
            
            # Log key metrics at appropriate verbosity level
            self.log_message(
                f"Validation accuracy: {formatted_metrics.get('val_accuracy', 'N/A'):.4f}", 
                VerbosityLevel.PROGRESS
            )
            self.log_message(
                f"Test accuracy: {formatted_metrics.get('test_accuracy', 'N/A'):.4f}", 
                VerbosityLevel.PROGRESS
            )
            self.log_message(
                f"Validation F1: {formatted_metrics.get('val_f1', 'N/A'):.4f}", 
                VerbosityLevel.PROGRESS
            )
            self.log_message(
                f"Test F1: {formatted_metrics.get('test_f1', 'N/A'):.4f}", 
                VerbosityLevel.PROGRESS
            )
            
            results['metrics'] = formatted_metrics
            
        except Exception as e:
            self.log_error(f"Error during model evaluation: {str(e)}", VerbosityLevel.WARNINGS)
            # Create minimal metrics to avoid breaking the flow
            results['metrics'] = {
                "val_accuracy": 0.0, 
                "test_accuracy": 0.0,
                "evaluation_error": str(e)
            }
            # We continue execution as we still want to save the model
        
        # Step 7: Save model artifacts and metrics
        self.log_message(f"Saving model artifacts to {model_dir}", VerbosityLevel.PROGRESS)
        
        # Save the model
        save_result = trainer.save(model_dir)
        
        # Save metrics to JSON file
        metrics_path = model_dir / "metrics.json"
        
        # Debug: Print types of all values in formatted_metrics
        self.log_message("Checking metrics for JSON serializability...", VerbosityLevel.WARNINGS)
        for key, value in formatted_metrics.items():
            self.log_message(f"  {key}: {type(value).__name__} = {str(value)[:50]}", VerbosityLevel.WARNINGS)
        
        try:
            with open(metrics_path, 'w') as f:
                json.dump(formatted_metrics, f, indent=2)
        except TypeError as e:
            # If JSON serialization fails, try to identify the problematic key
            self.log_error(f"JSON serialization error: {str(e)}", VerbosityLevel.WARNINGS)
            
            # Save only the successfully serializable metrics
            safe_metrics = {}
            for key, value in formatted_metrics.items():
                try:
                    json.dumps({key: value})  # Test if this specific key-value is serializable
                    safe_metrics[key] = value
                except TypeError as te:
                    self.log_warning(f"Skipping non-serializable metric: {key} (type: {type(value).__name__}, error: {te})", VerbosityLevel.WARNINGS)
            
            # Save the safe metrics
            with open(metrics_path, 'w') as f:
                json.dump(safe_metrics, f, indent=2)
        
        self.log_success(
            f"Model training complete for {team_slug}/{subject_slug}/{algorithm}",
            VerbosityLevel.PROGRESS
        )
        
        results['success'] = True
        return results

    def handle(self, *args, **options):
        """
        Execute the command.
        
        Args:
            *args: Additional positional arguments
            **options: The parsed command arguments
        """
        # Set up verboser with the requested verbosity level
        self.setup_verboser(options["verbose"])
        
        # Run debug checks if --debug flag is set
        if options.get("debug"):
            self.stdout.write("=== Debug Mode Enabled ===\n")
            self.stdout.write("Checking ML imports...\n")
            _check_ml_imports(stdout=self.stdout)
            self.stdout.write("\n")
            
            # Show GPU info
            try:
                from gregory.ml.gpu_config import get_device_info
                device_info = get_device_info()
                self.stdout.write("Device info:\n")
                self.stdout.write(f"  Platform: {device_info.get('platform')}\n")
                self.stdout.write(f"  GPUs: {device_info.get('gpus', [])}\n")
                self.stdout.write(f"  Using GPU: {device_info.get('using_gpu', False)}\n")
                self.stdout.write("\n")
            except Exception as e:
                self.stdout.write(f"Could not get device info: {e}\n\n")
        
        try:
            # Validate command arguments
            self.validate_arguments(options)
            
            # Get teams and subjects to process
            teams_subjects = self.get_teams_and_subjects(options)
            
            if not teams_subjects:
                self.log_error("No valid team-subject combinations found")
                return
                
            self.log_message(
                f"Will train models for {len(teams_subjects)} team(s) with "
                f"{sum(len(subjects) for _, subjects in teams_subjects)} subject(s)",
                VerbosityLevel.PROGRESS
            )
            
            window_days = options.get("lookback_days", 90)  # Default is 90 days
            if options["all_articles"]:
                self.log_message("Using all articles (no time window)", VerbosityLevel.PROGRESS)
            else:
                self.log_message(
                    f"Using articles from the last {window_days} days", 
                    VerbosityLevel.PROGRESS
                )
                
            algos = options["parsed_algos"]
            self.log_message(
                f"Training algorithms: {', '.join(algos)} with probability threshold {options['prob_threshold']}", 
                VerbosityLevel.PROGRESS
            )
            
            # Initialize metrics collection for summary
            all_results = []
            
            # Main training loop
            for team_slug, subject_slugs in teams_subjects:
                # Get team object
                team = Team.objects.get(slug=team_slug)
                
                for subject_slug in subject_slugs:
                    # Get subject object
                    subject = Subject.objects.get(subject_slug=subject_slug, team=team)
                    
                    # Skip subjects that don't have auto_predict enabled
                    if not subject.auto_predict:
                        self.log_message(
                            f"Skipping {team_slug}/{subject_slug}: auto_predict not enabled",
                            VerbosityLevel.PROGRESS
                        )
                        continue
                    
                    self.log_message(
                        f"\nProcessing team='{team_slug}' subject='{subject_slug}'",
                        VerbosityLevel.PROGRESS
                    )
                    
                    # Process each algorithm
                    for algo in algos:
                        # Create a PredictionRunLog entry with success=None
                        with transaction.atomic():
                            run_log = PredictionRunLog.objects.create(
                                team=team,
                                subject=subject,
                                algorithm=algo,
                                run_type='train',
                                success=None,
                                model_version="pending",  # Will be updated after training
                                triggered_by=f"train_models command ({os.getenv('USER', 'system')})"
                            )
                        
                        try:
                            # Run the training pipeline
                            results = self.run_training_pipeline(
                                team_slug=team_slug,
                                subject_slug=subject_slug,
                                algorithm=algo,
                                options=options
                            )
                            
                            # Update run log with success and model version
                            with transaction.atomic():
                                run_log.success = True
                                run_log.run_finished = timezone.now()
                                run_log.model_version = results['model_version']
                                run_log.save()
                            
                            # Add to results for summary
                            all_results.append(results)
                            
                        except Exception as e:
                            # Log the error and mark as failed
                            error_msg = f"Training failed for {team_slug}/{subject_slug}/{algo}: {str(e)}"
                            stack_trace = traceback.format_exc()
                            self.log_error(error_msg, VerbosityLevel.PROGRESS)
                            self.log_error(stack_trace, VerbosityLevel.WARNINGS)
                            
                            # Update run log with failure - wrap in try/except to handle DB issues
                            try:
                                with transaction.atomic():
                                    run_log.success = False
                                    run_log.run_finished = timezone.now()
                                    # Trim error message to avoid overflow
                                    run_log.error_message = f"{error_msg}\n\n{stack_trace[:1000]}"  # Limit length
                                    run_log.save()
                            except Exception as db_error:
                                self.log_error(f"Failed to update run log: {str(db_error)}", VerbosityLevel.WARNINGS)
            
            # Print summary if verbosity level is high enough
            if all_results:
                # Build the summary table
                # Header row
                header = "{:<15} {:<20} {:<15} {:<10} {:<10} {:<10} {:<10}".format(
                    "Team", "Subject", "Algorithm", "Val Acc", "Test Acc", "Val F1", "Test F1"
                )
                separator = "-" * len(header)
                
                table_lines = [
                    separator,
                    header,
                    separator
                ]
                
                # Add rows for each result
                for result in all_results:
                    metrics = result.get('metrics', {})
                    
                    # Extract key metrics with default 'N/A' for missing values
                    val_acc = metrics.get('val_accuracy', 'N/A')
                    test_acc = metrics.get('test_accuracy', 'N/A')
                    val_f1 = metrics.get('val_f1', 'N/A')
                    test_f1 = metrics.get('test_f1', 'N/A')
                    
                    # Format numeric values or keep 'N/A'
                    val_acc_fmt = f"{val_acc:.4f}" if isinstance(val_acc, (int, float)) else val_acc
                    test_acc_fmt = f"{test_acc:.4f}" if isinstance(test_acc, (int, float)) else test_acc
                    val_f1_fmt = f"{val_f1:.4f}" if isinstance(val_f1, (int, float)) else val_f1
                    test_f1_fmt = f"{test_f1:.4f}" if isinstance(test_f1, (int, float)) else test_f1
                    
                    # Format the row
                    row = "{:<15} {:<20} {:<15} {:<10} {:<10} {:<10} {:<10}".format(
                        result['team'][:15], 
                        result['subject'][:20],
                        result['algorithm'][:15],
                        val_acc_fmt,
                        test_acc_fmt,
                        val_f1_fmt,
                        test_f1_fmt
                    )
                    table_lines.append(row)
                
                table_lines.append(separator)
                table_lines.append(f"\nAll model artifacts saved to: {os.path.join(settings.BASE_DIR, 'models')}")
                
                # Use verboser to show the summary table
                self.verboser.summary("\n".join(table_lines))
            
            self.log_success(f"Command completed successfully", VerbosityLevel.PROGRESS)
                
        except Exception as e:
            self.log_error(f"Command failed: {str(e)}")
            raise
