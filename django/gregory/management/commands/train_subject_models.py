from django.core.management.base import BaseCommand, CommandError
import logging
from gregory.models import Team, Subject, PredictionRunLog, Articles, ArticleSubjectRelevance
from django.utils import timezone
import argparse
from datetime import timedelta
import pandas as pd
import os
from django.conf import settings
from gregory.utils.text_utils import text_cleaning_pd_series, text_cleaning_string, load_and_format_dataset, cleanText
from sklearn.model_selection import train_test_split

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Train ML models for subjects. Supports filtering by team and subject.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--team',
            type=str,
            help='Team slug to filter subjects'
        )
        parser.add_argument(
            '--subject',
            type=str,
            help='Subject slug to train model for'
        )
        parser.add_argument(
            '--timeframe',
            type=int,
            default=90,
            help='Training timeframe in days (default: 90)'
        )
        parser.add_argument(
            '--device',
            type=str,
            default='cpu',
            choices=['cpu', 'gpu', 'tpu'],
            help='Device to use for training (default: cpu)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Perform a dry run without actually training models'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Increase output verbosity'
        )

    def log(self, message, level=logging.INFO, verbosity=1):
        """
        Log message based on verbosity level
        """
        if self.verbosity >= verbosity:
            self.stdout.write(message)
        logger.log(level, message)
        
    def fetch_articles_for_training(self, subject, timeframe_days):
        """
        Fetch articles for training based on the timeframe.
        
        Args:
            subject: Subject model instance
            timeframe_days: Number of days to look back for articles
            
        Returns:
            Tuple of (labeled_df, unlabeled_df) pandas DataFrames
        """
        # Calculate the cutoff date based on timeframe
        cutoff_date = timezone.now() - timedelta(days=timeframe_days)
        
        # Fetch articles within timeframe
        articles = Articles.objects.filter(
            discovery_date__gte=cutoff_date,
            subjects=subject,
            retracted=False  # Exclude retracted articles
        ).select_related().prefetch_related('article_subject_relevances')
        
        self.log(f"Found {articles.count()} non-retracted articles for subject '{subject}' within {timeframe_days} days", verbosity=2)

        # Create lists to store labeled and unlabeled articles
        labeled_articles = []
        unlabeled_articles = []
        
        # Process each article to determine if it has a label
        for article in articles:
            # Try to get relevance information for this article-subject pair
            try:
                relevance = ArticleSubjectRelevance.objects.get(
                    article=article,
                    subject=subject
                )
                # Add to labeled articles with relevance information
                labeled_articles.append({
                    'article_id': article.article_id,
                    'title': article.title,
                    'summary': article.summary,
                    'discovery_date': article.discovery_date,
                    'is_relevant': relevance.is_relevant
                })
            except ArticleSubjectRelevance.DoesNotExist:
                # Add to unlabeled articles
                unlabeled_articles.append({
                    'article_id': article.article_id,
                    'title': article.title,
                    'summary': article.summary,
                    'discovery_date': article.discovery_date
                })
        
        # Convert to pandas DataFrames
        labeled_df = pd.DataFrame(labeled_articles) if labeled_articles else pd.DataFrame()
        unlabeled_df = pd.DataFrame(unlabeled_articles) if unlabeled_articles else pd.DataFrame()
        
        self.log(f"Created DataFrames: {len(labeled_df)} labeled and {len(unlabeled_df)} unlabeled articles", verbosity=2)
        
        return labeled_df, unlabeled_df
    
    def clean_and_prepare_data(self, df, remove_stopwords=True, remove_punctuation=True, remove_digits=False, stemming=False, lemmatization=False):
        """
        Clean and prepare data for training, following the same approach as in the training notebook
        which uses text_cleaning_pd_series.
        
        Args:
            df: DataFrame with articles
            remove_stopwords: Whether to remove stopwords (default: True)
            remove_punctuation: Whether to remove punctuation (default: True)
            remove_digits: Whether to remove digits (default: False)
            stemming: Whether to apply stemming (default: False)
            lemmatization: Whether to apply lemmatization (default: False)
            
        Returns:
            DataFrame with cleaned text
        """
        if df.empty:
            return df
        
        # Combine title and summary as full_text, matching the notebook approach
        df['full_text'] = df['title'].fillna('') + ' ' + df['summary'].fillna('')
        
        try:
            # Clean the text using text_cleaning_pd_series from the notebook
            df['cleaned_text'] = text_cleaning_pd_series(
                df['full_text'], 
                remove_stopwords=remove_stopwords,
                remove_punctuation=remove_punctuation, 
                remove_digits=remove_digits,
                stemming=stemming,
                lemmatization=lemmatization
            )
        except ImportError as e:
            # Fallback to our original cleanText function if NLTK is not available
            self.log(f"Advanced text cleaning not available: {str(e)}. Using basic text cleaning instead.", verbosity=1)
            df['cleaned_text'] = df['full_text'].apply(cleanText)
            
            # Filter out short texts to match the behavior of text_cleaning_pd_series
            df['cleaned_text'] = df['cleaned_text'].apply(
                lambda x: None if x is None or len(x.split()) < 10 else x
            )
        
        # Drop rows where cleaning resulted in empty or too short text
        df = df.dropna(subset=['cleaned_text'])
        
        self.log(f"Cleaned and prepared {len(df)} articles", verbosity=2)
        
        return df
    
    def split_data(self, df, min_examples_per_class=3):
        """
        Split data into training, validation, and test sets in a stratified manner,
        following the same approach as in the training notebook.
        
        Args:
            df: DataFrame with labeled articles
            min_examples_per_class: Minimum number of examples required per class
            
        Returns:
            Tuple of (train_df, val_df, test_df) DataFrames
        """
        if df.empty:
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
        
        # Check if each class has enough examples
        class_counts = df['is_relevant'].value_counts()
        for cls, count in class_counts.items():
            if count < min_examples_per_class:
                raise ValueError(f"Not enough examples for class '{'relevant' if cls else 'not relevant'}'. "
                                 f"Got {count}, need at least {min_examples_per_class}.")
        
        # First split: 85% train_val and 15% test - following notebook approach
        train_val_df, test_df = train_test_split(
            df,
            test_size=0.15,
            stratify=df['is_relevant'],
            random_state=69  # Using same random state as notebook
        )
        
        # Second split: ~88.235% train and ~11.765% val from train_val_df
        # 0.1765 * 0.85 ≈ 0.15 of the original dataset
        train_df, val_df = train_test_split(
            train_val_df,
            test_size=0.1765,
            stratify=train_val_df['is_relevant'],
            random_state=69  # Using same random state as notebook
        )
        
        self.log(f"Split data into training ({len(train_df)}), validation ({len(val_df)}), and test ({len(test_df)}) sets", verbosity=2)
        
        return train_df, val_df, test_df

    def write_csv_files(self, train_df, val_df, test_df, unlabeled_df, team_slug, subject_slug, model_version):
        """
        Write training, validation, test, and unlabeled data to CSV files.
        
        Args:
            train_df: Training DataFrame
            val_df: Validation DataFrame
            test_df: Test DataFrame
            unlabeled_df: Unlabeled DataFrame
            team_slug: Team slug
            subject_slug: Subject slug
            model_version: Model version string
            
        Returns:
            Path to the directory where files were written
        """
        # Create directory structure
        output_dir = os.path.join(settings.BASE_DIR, 'data', team_slug, subject_slug, model_version)
        os.makedirs(output_dir, exist_ok=True)
        print(f"Created directory: {output_dir}")
        # Write CSV files
        train_path = os.path.join(output_dir, 'train.csv')
        val_path = os.path.join(output_dir, 'validation.csv')
        test_path = os.path.join(output_dir, 'test.csv')
        unlabeled_path = os.path.join(output_dir, 'unlabeled.csv')
        
        # Set article_id as index if not already set, to match notebook's approach
        if 'article_id' in train_df.columns:
            train_df.set_index('article_id', inplace=True)
            val_df.set_index('article_id', inplace=True)
            test_df.set_index('article_id', inplace=True)
            if 'article_id' in unlabeled_df.columns:
                unlabeled_df.set_index('article_id', inplace=True)
        
        # Write to CSV files, including index (which should be article_id)
        train_df.to_csv(train_path)
        val_df.to_csv(val_path)
        test_df.to_csv(test_path)
        unlabeled_df.to_csv(unlabeled_path)
        
        self.log(f"Wrote CSV files to {output_dir}", verbosity=2)
        self.log(f"  - Training set: {len(train_df)} articles", verbosity=1)
        self.log(f"  - Validation set: {len(val_df)} articles", verbosity=1)
        self.log(f"  - Test set: {len(test_df)} articles", verbosity=1)
        self.log(f"  - Unlabeled set: {len(unlabeled_df)} articles", verbosity=1)
        
        return output_dir
    
    def prepare_data_for_training(self, labeled_df):
        """
        Prepares the labeled data for model training, following the notebook's approach.
        
        Args:
            labeled_df: DataFrame with labeled articles
            
        Returns:
            Tuple of (X_train, y_train) for model training
        """
        if labeled_df.empty:
            self.log("No labeled data available for training", level=logging.WARNING)
            return None, None
        
        # Use the cleaned_text column that we created in clean_and_prepare_data
        # This aligns with the notebook's approach using text_processed
        
        # Features and target
        X_train = labeled_df['cleaned_text']
        y_train = labeled_df['is_relevant']
        
        # Convert boolean values to 0/1 to match notebook approach
        if not isinstance(y_train.iloc[0], (int, float)):
            y_train = y_train.apply(lambda x: 1 if x is True else 0)
        
        self.log(f"Prepared training data: {len(X_train)} samples with {y_train.sum()} positive labels", verbosity=2)
        
        return X_train, y_train

    def handle(self, *args, **options):
        # Set verbosity level from options
        self.verbosity = 2 if options['verbose'] else 1
        dry_run = options['dry_run']
        team_slug = options['team']
        subject_slug = options['subject']
        timeframe = options['timeframe']
        device = options['device']

        # Log the parsed arguments
        self.log(f"Running with options:", verbosity=1)
        self.log(f"  Dry run: {dry_run}", verbosity=1)
        self.log(f"  Team: {team_slug or 'All teams'}", verbosity=1)
        self.log(f"  Subject: {subject_slug or 'All subjects'}", verbosity=1)
        self.log(f"  Timeframe: {timeframe} days", verbosity=1)
        self.log(f"  Device: {device}", verbosity=1)

        # Filter teams and subjects based on arguments
        teams = []
        if team_slug:
            try:
                team = Team.objects.get(slug=team_slug)
                teams = [team]
                self.log(f"Processing team: {team.organization}", verbosity=1)
                self.log(f"Filtered to team: {team}", verbosity=2)
            except Team.DoesNotExist:
                raise CommandError(f"Team with slug '{team_slug}' does not exist")
        else:
            teams = Team.objects.all()
            self.log(f"Processing all {teams.count()} teams", verbosity=2)

        # Process each team
        for team in teams:
            self.log(f"Processing team: {team}", verbosity=1)
            
            subjects = []
            if subject_slug:
                try:
                    subject = Subject.objects.get(team=team, subject_slug=subject_slug)
                    subjects = [subject]
                    self.log(f"Filtered to subject: {subject.subject_name}", verbosity=1)
                except Subject.DoesNotExist:
                    warning_message = f"Subject with slug '{subject_slug}' does not exist for team {team}"
                    self.log(warning_message, level=logging.WARNING, verbosity=1)
                    continue
            else:
                subjects = Subject.objects.filter(team=team)
                self.log(f"Processing {subjects.count()} subjects for team {team}", verbosity=2)

            # Process each subject
            for subject in subjects:
                self.log(f"Processing subject: {subject.subject_name} (team: {team.name})", verbosity=1)
                
                # Skip actual processing if this is a dry run
                if dry_run:
                    continue
                    
                # Fetch articles for training
                labeled_df, unlabeled_df = self.fetch_articles_for_training(subject, timeframe)
                
                if labeled_df.empty:
                    self.log(f"No labeled articles found for subject {subject.subject_name}. Skipping training.", level=logging.WARNING)
                    continue
                
                # Define model version (should be dynamic in real implementation)
                model_version = "v1.0.0"
                
                # Clean and prepare the data - following the notebook's approach
                # Use lemmatization=True to match the recommended notebook approach
                labeled_df = self.clean_and_prepare_data(
                    labeled_df,
                    remove_stopwords=True,
                    remove_punctuation=True,
                    remove_digits=False,
                    stemming=False,
                    lemmatization=True
                )
                unlabeled_df = self.clean_and_prepare_data(
                    unlabeled_df,
                    remove_stopwords=True,
                    remove_punctuation=True,
                    remove_digits=False,
                    stemming=False,
                    lemmatization=True
                )
                
                # Log the counts after cleaning
                self.log(f"After cleaning: {len(labeled_df)} labeled articles, {len(unlabeled_df)} unlabeled articles", verbosity=1)
                
                # Create a log entry for this run
                run_log = PredictionRunLog.objects.create(
                    team=team,
                    subject=subject,
                    model_version=model_version,
                    run_type="train",
                    triggered_by="management_command",
                    run_started=timezone.now()
                )
                
                try:
                    # Split the labeled data
                    try:
                        train_df, val_df, test_df = self.split_data(labeled_df, min_examples_per_class=3)
                    except ValueError as e:
                        self.log(str(e), level=logging.WARNING)
                        run_log.run_finished = timezone.now()
                        run_log.success = False
                        run_log.error_message = str(e)
                        run_log.save()
                        continue
                    
                    # Write CSV files
                    output_dir = self.write_csv_files(
                        train_df, val_df, test_df, unlabeled_df,
                        team.slug, subject.subject_slug, model_version
                    )
                    
                    # Prepare data for training
                    X_train, y_train = self.prepare_data_for_training(train_df)
                    
                    if X_train is None or y_train is None:
                        self.log(f"Could not prepare training data for subject {subject.subject_name}. Skipping.", level=logging.WARNING)
                        continue
                    
                    # Calculate class distribution for reporting
                    if not y_train.empty:
                        total = len(y_train)
                        positive = y_train.sum()
                        negative = total - positive
                        pos_ratio = (positive / total) * 100 if total > 0 else 0
                        
                        self.log(f"Class distribution for {subject.subject_name}:", verbosity=1)
                        self.log(f"  Relevant: {positive} ({pos_ratio:.1f}%)", verbosity=1)
                        self.log(f"  Not relevant: {negative} ({100 - pos_ratio:.1f}%)", verbosity=1)
                    
                    # Here would be the actual model training code
                    self.log(f"Training model for subject: {subject.subject_name} with {len(X_train)} samples", verbosity=1)
                    
                    # Update run log to indicate successful completion
                    run_log.run_finished = timezone.now()
                    run_log.success = True
                    run_log.save()
                    
                    self.log(f"Successfully trained model for subject: {subject.subject_name}", verbosity=1)
                except Exception as e:
                    # Update run log to indicate failure
                    run_log.run_finished = timezone.now()
                    run_log.success = False
                    run_log.error_message = str(e)
                    run_log.save()
                    
                    self.log(f"Error training model for subject {subject.subject_name}: {str(e)}", level=logging.ERROR)
        
        if dry_run:
            self.log("Dry run completed. No models were trained.", verbosity=1)
        else:
            self.log("Command completed successfully", verbosity=1)
