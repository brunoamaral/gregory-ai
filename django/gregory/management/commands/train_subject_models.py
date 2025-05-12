from django.core.management.base import BaseCommand, CommandError
import logging
from gregory.models import Team, Subject, PredictionRunLog, Articles, ArticleSubjectRelevance
from django.utils import timezone
import argparse
from datetime import timedelta
import pandas as pd
import os
import json
import time
from django.conf import settings
from gregory.utils.text_utils import text_cleaning_pd_series, text_cleaning_string, load_and_format_dataset, cleanText
from sklearn.model_selection import train_test_split
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from gregory.utils.pseudo_labeling import get_pseudo_labeled_data
from gregory.utils.bert_model import BERTClassifier
from gregory.utils.lgbm_tfidf import LGBMTfidfClassifier
import time

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = '''Manage ML models for subjects.
    
    Commands:
      train  - Train ML models for subjects. Supports filtering by team/subject.
               Pseudo-labeling is enabled by default.
               
      predict - Use trained models to make predictions on new text data.
    
    Examples:
      # Train a logistic regression model for a specific team and subject
      python manage.py train_subject_models train --team=example --subject=healthcare
      
      # Train a BERT model with pseudo-labeling and export metadata
      python manage.py train_subject_models train --team=example --subject=healthcare --model-type=bert --export-metadata
      
      # Train all model types without pseudo-labeling
      python manage.py train_subject_models train --team=example --subject=healthcare --model-type=all --no-pseudo-labeling
      
      # Make a prediction using a trained model
      python manage.py train_subject_models predict --team=example --subject=healthcare --input-text="This is a sample text to classify"
      
      # Process a file with texts and save predictions to a JSON file
      python manage.py train_subject_models predict --team=example --subject=healthcare --input-file=texts.csv --output-file=predictions.json
    '''

    def add_arguments(self, parser):
        # Create subparsers for different commands
        subparsers = parser.add_subparsers(dest='command', help='Command to run')
        
        # Train command
        train_parser = subparsers.add_parser('train', help='Train ML models for subjects')
        
        # Common arguments
        for p in [train_parser, parser]:  # Add to both main parser and train subparser for backwards compatibility
            p.add_argument(
                '--team',
                type=str,
                help='Team slug to filter subjects'
            )
            p.add_argument(
                '--subject',
                type=str,
                help='Subject slug to train model for'
            )
            p.add_argument(
                '--verbose',
                action='store_true',
                help='Increase output verbosity'
            )
            p.add_argument(
                '--model-type',
                type=str,
                default='logreg',
                choices=['logreg', 'lgbm', 'bert', 'all'],
                help='Model type to use: logistic regression, LightGBM with TF-IDF, BERT, or all (default: logreg)'
            )
        
        # Training-specific arguments
        train_parser.add_argument(
            '--timeframe',
            type=int,
            default=90,
            help='Training timeframe in days (default: 90)'
        )
        train_parser.add_argument(
            '--device',
            type=str,
            default='cpu',
            choices=['cpu', 'gpu', 'tpu'],
            help='Device to use for training (default: cpu)'
        )
        train_parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Perform a dry run without actually training models'
        )
        train_parser.add_argument(
            '--no-pseudo-labeling',
            action='store_true',
            help='Disable pseudo-labeling of unlabeled data during training (enabled by default)'
        )
        train_parser.add_argument(
            '--pseudo-confidence',
            type=float,
            default=0.9,
            help='Confidence threshold for pseudo-labeling (default: 0.9)'
        )
        train_parser.add_argument(
            '--bert-max-len',
            type=int,
            default=128,
            help='Maximum sequence length for BERT model (default: 128)'
        )
        train_parser.add_argument(
            '--export-metadata',
            action='store_true',
            help='Export model metadata as JSON'
        )
        
        # Predict command
        predict_parser = subparsers.add_parser('predict', help='Make predictions using trained models')
        predict_parser.add_argument(
            '--team',
            type=str,
            required=True,
            help='Team slug to use for prediction'
        )
        predict_parser.add_argument(
            '--subject',
            type=str,
            required=True,
            help='Subject slug to use for prediction'
        )
        predict_parser.add_argument(
            '--model-type',
            type=str,
            default='logreg',
            choices=['logreg', 'lgbm', 'bert'],
            help='Model type to use for prediction (default: logreg)'
        )
        predict_parser.add_argument(
            '--model-version',
            type=str,
            default='v1.0.0',
            help='Model version to use for prediction (default: v1.0.0)'
        )
        predict_parser.add_argument(
            '--input-text',
            type=str,
            help='Text to classify'
        )
        predict_parser.add_argument(
            '--input-file',
            type=str,
            help='Path to CSV or JSON file with texts to classify'
        )
        predict_parser.add_argument(
            '--output-file',
            type=str,
            help='Path to output file for predictions (default: stdout)'
        )
        predict_parser.add_argument(
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
        
    def apply_pseudo_labeling(self, train_df, unlabeled_df, confidence_threshold=0.9):
        """
        Apply pseudo-labeling to unlabeled data and combine with training data.
        
        This process:
        1. Loads unlabeled data and applies threshold logic (prob ≥ confidence_threshold or ≤ 1-confidence_threshold)
        2. Caps pseudo-labeled examples per class to match hand-labeled examples
        3. Selects highest-confidence examples when capping
        
        Args:
            train_df: DataFrame with labeled training data
            unlabeled_df: DataFrame with unlabeled data
            confidence_threshold: Confidence threshold for pseudo-labeling (default: 0.9)
            
        Returns:
            DataFrame with combined labeled and pseudo-labeled data
        """
        if train_df.empty or unlabeled_df.empty:
            self.log("Not enough data for pseudo-labeling", level=logging.WARNING)
            return train_df
            
        self.log(f"Applying pseudo-labeling with confidence threshold {confidence_threshold}", verbosity=1)
        self.log(f"Initial training data: {len(train_df)} samples", verbosity=2)
        self.log(f"Available unlabeled data: {len(unlabeled_df)} samples", verbosity=2)
        
        # Prepare initial training data
        X_train, y_train = self.prepare_data_for_training(train_df)
        if X_train is None or len(X_train) == 0:
            return train_df
            
        # Initialize and fit a vectorizer
        vectorizer = TfidfVectorizer()
        X_train_vec = vectorizer.fit_transform(X_train)
        
        # Train a simple model for pseudo-labeling
        model = LogisticRegression(max_iter=1000)
        model.fit(X_train_vec, y_train)
        
        # Apply pseudo-labeling
        pseudo_df = get_pseudo_labeled_data(
            unlabeled_df, 
            model, 
            vectorizer, 
            train_df, 
            confidence_threshold
        )
        
        if pseudo_df.empty:
            self.log("No samples met the pseudo-labeling criteria", verbosity=1)
            return train_df
            
        # Combine original and pseudo-labeled data
        combined_df = pd.concat([train_df, pseudo_df])
        
        self.log(f"Added {len(pseudo_df)} pseudo-labeled samples", verbosity=1)
        self.log(f"  - Relevant: {pseudo_df['is_relevant'].sum()}", verbosity=2)
        self.log(f"  - Not relevant: {len(pseudo_df) - pseudo_df['is_relevant'].sum()}", verbosity=2)
        self.log(f"Combined training data: {len(combined_df)} samples", verbosity=1)
        
        return combined_df

    def train_lgbm_model(self, X_train, y_train, X_val=None, y_val=None):
        """
        Train a LightGBM model with TF-IDF features.
        
        Args:
            X_train: Training features
            y_train: Training labels
            X_val: Validation features (optional)
            y_val: Validation labels (optional)
            
        Returns:
            Trained LGBMTfidfClassifier instance
        """
        self.log("Training LightGBM model with TF-IDF features...", verbosity=1)
        
        # Initialize the model
        lgbm_classifier = LGBMTfidfClassifier()
        
        # Train the model
        start_time = time.time()
        lgbm_classifier.train(X_train, y_train)
        
        # Evaluate on validation set if available
        if X_val is not None and y_val is not None:
            metrics = lgbm_classifier.evaluate(X_val, y_val)
            self.log(f"Validation metrics: {metrics}", verbosity=2)
        
        training_time = time.time() - start_time
        self.log(f"LightGBM model training completed in {training_time:.2f} seconds", verbosity=1)
        
        return lgbm_classifier
    
    def train_bert_model(self, X_train, y_train, X_val=None, y_val=None, max_len=128):
        """
        Train a BERT model for text classification.
        
        Args:
            X_train: Training features
            y_train: Training labels
            X_val: Validation features (optional)
            y_val: Validation labels (optional)
            max_len: Maximum sequence length for BERT
            
        Returns:
            Trained BERTClassifier instance
        """
        self.log("Training BERT model...", verbosity=1)
        
        # Initialize the model
        bert_classifier = BERTClassifier(max_len=max_len)
        
        # Train the model with default parameters
        start_time = time.time()
        history = bert_classifier.train(
            X_train, 
            y_train,
            X_val=X_val,
            y_val=y_val,
            batch_size=16,
            epochs=4,
            patience=2
        )
        
        training_time = time.time() - start_time
        self.log(f"BERT model training completed in {training_time:.2f} seconds", verbosity=1)
        
        return bert_classifier
    
    def train_logreg_model(self, X_train, y_train):
        """
        Train a Logistic Regression model with TF-IDF features.
        
        Args:
            X_train: Training features
            y_train: Training labels
            
        Returns:
            Tuple of (vectorizer, model)
        """
        self.log("Training Logistic Regression model with TF-IDF features...", verbosity=1)
        
        # Initialize and fit vectorizer
        vectorizer = TfidfVectorizer()
        X_train_vec = vectorizer.fit_transform(X_train)
        
        # Train logistic regression model
        model = LogisticRegression(max_iter=1000)
        model.fit(X_train_vec, y_train)
        
        return vectorizer, model

    def save_model(self, model, model_type, team_slug, subject_slug, model_version, include_metadata=True):
        """
        Save a trained model to disk.
        
        Args:
            model: Trained model instance
            model_type: Type of model ('logreg', 'lgbm', or 'bert')
            team_slug: Team slug
            subject_slug: Subject slug
            model_version: Model version string
            include_metadata: Whether to include metadata (default: True)
            
        Returns:
            dict: Paths to saved model files
        """
        # Create directory for the model
        model_dir = os.path.join(settings.BASE_DIR, 'data', team_slug, subject_slug, model_version, model_type)
        os.makedirs(model_dir, exist_ok=True)
        
        # Save the model based on its type
        if model_type == 'logreg':
            # Unpack the tuple of (vectorizer, model)
            vectorizer, logreg_model = model
            
            # Save the vectorizer and model
            vectorizer_path = os.path.join(model_dir, 'tfidf_vectorizer.joblib')
            model_path = os.path.join(model_dir, 'logreg_model.joblib')
            
            # Save using joblib
            import joblib
            joblib.dump(vectorizer, vectorizer_path)
            joblib.dump(logreg_model, model_path)
            
            # Create metadata
            if include_metadata:
                metadata = {
                    'model_type': 'LogisticRegression_TFIDF',
                    'created_at': time.strftime("%Y-%m-%d %H:%M:%S"),
                    'parameters': {
                        'max_iter': logreg_model.max_iter,
                    },
                    'vectorizer_params': {
                        'features': vectorizer.get_feature_names_out().tolist()[:10] + ['...'],  # First 10 features
                        'num_features': len(vectorizer.get_feature_names_out())
                    }
                }
                
                # Save metadata as JSON
                metadata_path = os.path.join(model_dir, 'metadata.json')
                with open(metadata_path, 'w') as f:
                    json.dump(metadata, f, indent=2)
                
                return {
                    'vectorizer_path': vectorizer_path,
                    'model_path': model_path,
                    'metadata_path': metadata_path
                }
            
            return {
                'vectorizer_path': vectorizer_path,
                'model_path': model_path
            }
            
        elif model_type == 'lgbm':
            # Save the LightGBM model
            return model.save_model(model_dir)
            
        elif model_type == 'bert':
            # Save the BERT model
            model.save_model(model_dir)
            return {'model_dir': model_dir}
        
        else:
            raise ValueError(f"Unknown model type: {model_type}")

    def load_model(self, model_type, team_slug, subject_slug, model_version):
        """
        Load a trained model from disk.
        
        Args:
            model_type: Type of model ('logreg', 'lgbm', or 'bert')
            team_slug: Team slug
            subject_slug: Subject slug
            model_version: Model version string
            
        Returns:
            Loaded model object with metadata
        """
        # Determine path to the model
        model_dir = os.path.join(settings.BASE_DIR, 'data', team_slug, subject_slug, model_version, model_type)
        
        if not os.path.exists(model_dir):
            raise FileNotFoundError(f"Model directory not found: {model_dir}")
            
        self.log(f"Loading {model_type} model from {model_dir}", verbosity=1)
        
        # Load metadata if available
        metadata = None
        metadata_path = os.path.join(model_dir, 'metadata.json')
        if os.path.exists(metadata_path):
            with open(metadata_path, 'r') as f:
                metadata = json.load(f)
            self.log(f"Loaded model metadata from {metadata_path}", verbosity=2)
            
        # Load the model based on its type
        if model_type == 'logreg':
            # Load the vectorizer and logistic regression model
            import joblib
            
            # Check for required files
            vectorizer_path = os.path.join(model_dir, 'tfidf_vectorizer.joblib')
            model_path = os.path.join(model_dir, 'logreg_model.joblib')
            
            if not os.path.exists(vectorizer_path) or not os.path.exists(model_path):
                raise FileNotFoundError(f"Required model files not found in {model_dir}")
                
            # Load the models
            vectorizer = joblib.load(vectorizer_path)
            logreg_model = joblib.load(model_path)
            
            self.log(f"Loaded logistic regression model and vectorizer", verbosity=2)
            
            # Return the loaded models and metadata
            return (vectorizer, logreg_model), metadata
            
        elif model_type == 'lgbm':
            # Create an instance of LGBMTfidfClassifier and load the model
            from gregory.utils.lgbm_tfidf import LGBMTfidfClassifier
            
            # Create a new instance
            lgbm_classifier = LGBMTfidfClassifier()
            
            # Load the saved model
            lgbm_classifier.load_model(model_dir)
            
            self.log(f"Loaded LightGBM model with TF-IDF vectorizer", verbosity=2)
            
            return lgbm_classifier, metadata
            
        elif model_type == 'bert':
            # Create an instance of BERTClassifier and load the model
            from gregory.utils.bert_model import BERTClassifier
            
            # Create a new instance
            # If max_len is in metadata, use it, otherwise default to 128
            max_len = metadata.get('max_len', 128) if metadata else 128
            bert_classifier = BERTClassifier(max_len=max_len)
            
            # Load the saved model
            bert_classifier.load_model(model_dir)
            
            self.log(f"Loaded BERT model", verbosity=2)
            
            return bert_classifier, metadata
            
        else:
            raise ValueError(f"Unknown model type: {model_type}")
            
    def predict(self, model_type, model, texts):
        """
        Make predictions with a loaded model.
        
        Args:
            model_type: Type of model ('logreg', 'lgbm', or 'bert')
            model: Loaded model object
            texts: List of text strings to predict on
            
        Returns:
            Dictionary with predictions and probabilities
        """
        self.log(f"Making predictions with {model_type} model on {len(texts)} texts", verbosity=1)
        
        if model_type == 'logreg':
            # Unpack the tuple of (vectorizer, model)
            vectorizer, logreg_model = model
            
            # Vectorize the texts
            X = vectorizer.transform(texts)
            
            # Make predictions
            probs = logreg_model.predict_proba(X)
            preds = logreg_model.predict(X)
            
            return {
                'predictions': preds.tolist(),
                'probabilities': probs[:, 1].tolist()  # Probability of positive class
            }
            
        elif model_type == 'lgbm':
            # Use the model's predict method
            return model.predict(texts)
            
        elif model_type == 'bert':
            # Use the model's predict method
            return model.predict(texts)
            
        else:
            raise ValueError(f"Unknown model type: {model_type}")

    def handle(self, *args, **options):
        # Set verbosity level from options
        self.verbosity = 2 if options.get('verbose', False) else 1
        
        # Get the command to run (default to 'train' for backwards compatibility)
        command = options.get('command', 'train')
        
        # Get common options
        team_slug = options.get('team')
        subject_slug = options.get('subject')
        model_type = options.get('model_type', 'logreg')
        
        if command == 'train':
            self.handle_train_command(options)
        elif command == 'predict':
            self.handle_predict_command(options)
        else:
            self.log(f"Unknown command: {command}", level=logging.ERROR)
            raise CommandError(f"Unknown command: {command}")

    def handle_train_command(self, options):
        """Handle the 'train' command."""
        # Get training-specific options
        dry_run = options.get('dry_run', False)
        team_slug = options.get('team')
        subject_slug = options.get('subject')
        timeframe = options.get('timeframe', 90)
        device = options.get('device', 'cpu')
        use_pseudo_labeling = not options.get('no_pseudo_labeling', False)  # Enabled by default
        pseudo_confidence = options.get('pseudo_confidence', 0.9)
        model_type = options.get('model_type', 'logreg')
        bert_max_len = options.get('bert_max_len', 128)
        export_metadata = options.get('export_metadata', False)

        # Log the parsed arguments
        self.log(f"Running train command with options:", verbosity=1)
        self.log(f"  Dry run: {dry_run}", verbosity=1)
        self.log(f"  Team: {team_slug or 'All teams'}", verbosity=1)
        self.log(f"  Subject: {subject_slug or 'All subjects'}", verbosity=1)
        self.log(f"  Timeframe: {timeframe} days", verbosity=1)
        self.log(f"  Device: {device}", verbosity=1)
        self.log(f"  Model type: {model_type}", verbosity=1)
        self.log(f"  BERT max sequence length: {bert_max_len}", verbosity=1)
        self.log(f"  Export metadata: {'Enabled' if export_metadata else 'Disabled'}", verbosity=1)
        if use_pseudo_labeling:
            self.log(f"  Pseudo-labeling: Enabled (confidence threshold: {pseudo_confidence})", verbosity=1)
        else:
            self.log(f"  Pseudo-labeling: Disabled", verbosity=1)

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
                    
                    # Apply pseudo-labeling if enabled
                    original_train_size = len(train_df)
                    if use_pseudo_labeling and not unlabeled_df.empty:
                        self.log(f"Applying pseudo-labeling with {len(unlabeled_df)} unlabeled samples", verbosity=1)
                        train_df = self.apply_pseudo_labeling(train_df, unlabeled_df, pseudo_confidence)
                        self.log(f"Training data increased from {original_train_size} to {len(train_df)} samples", verbosity=1)
                    
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
                    if not train_df.empty:
                        total = len(train_df)
                        positive = train_df['is_relevant'].sum()
                        negative = total - positive
                        pos_ratio = (positive / total) * 100 if total > 0 else 0
                        
                        self.log(f"Class distribution for {subject.subject_name}:", verbosity=1)
                        self.log(f"  Relevant: {positive} ({pos_ratio:.1f}%)", verbosity=1)
                        self.log(f"  Not relevant: {negative} ({100 - pos_ratio:.1f}%)", verbosity=1)
                    
                    # Train models based on the selected type
                    if model_type in ['logreg', 'all']:
                        vectorizer, logreg_model = self.train_logreg_model(X_train, y_train)
                        self.log(f"Logistic Regression model trained for subject: {subject.subject_name}", verbosity=1)
                        self.save_model((vectorizer, logreg_model), 'logreg', team.slug, subject.subject_slug, model_version, include_metadata=export_metadata)
                    
                    if model_type in ['lgbm', 'all']:
                        lgbm_model = self.train_lgbm_model(X_train, y_train, X_val=val_df['cleaned_text'], y_val=val_df['is_relevant'])
                        self.log(f"LightGBM model trained for subject: {subject.subject_name}", verbosity=1)
                        self.save_model(lgbm_model, 'lgbm', team.slug, subject.subject_slug, model_version, include_metadata=export_metadata)
                    
                    if model_type in ['bert', 'all']:
                        bert_model = self.train_bert_model(X_train, y_train, X_val=val_df['cleaned_text'], y_val=val_df['is_relevant'], max_len=bert_max_len)
                        self.log(f"BERT model trained for subject: {subject.subject_name}", verbosity=1)
                        self.save_model(bert_model, 'bert', team.slug, subject.subject_slug, model_version, include_metadata=export_metadata)
                    
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

    def handle_predict_command(self, options):
        """Handle the 'predict' command to make predictions using trained models."""
        # Get prediction-specific options
        team_slug = options.get('team')
        subject_slug = options.get('subject')
        model_type = options.get('model_type', 'logreg')
        model_version = options.get('model_version', 'v1.0.0')
        input_text = options.get('input_text')
        input_file = options.get('input_file')
        output_file = options.get('output_file')
        
        # Log the parsed arguments
        self.log(f"Running predict command with options:", verbosity=1)
        self.log(f"  Team: {team_slug}", verbosity=1)
        self.log(f"  Subject: {subject_slug}", verbosity=1)
        self.log(f"  Model type: {model_type}", verbosity=1)
        self.log(f"  Model version: {model_version}", verbosity=1)
        self.log(f"  Input text: {input_text if input_text else 'None'}", verbosity=1)
        self.log(f"  Input file: {input_file if input_file else 'None'}", verbosity=1)
        self.log(f"  Output file: {output_file if output_file else 'stdout'}", verbosity=1)
        
        # Validate input options
        if not input_text and not input_file:
            raise CommandError("Either --input-text or --input-file must be provided")
        
        if input_text and input_file:
            self.log("Both input text and input file provided. Using input text.", level=logging.WARNING)
        
        # Load the model
        try:
            model, metadata = self.load_model(model_type, team_slug, subject_slug, model_version)
            
            if metadata:
                self.log(f"Loaded model metadata: {json.dumps(metadata, indent=2)}", verbosity=2)
        except Exception as e:
            raise CommandError(f"Failed to load model: {str(e)}")
        
        # Prepare input texts
        texts = []
        texts_source = None
        
        if input_text:
            texts = [input_text]
            texts_source = 'command_line'
            self.log(f"Using input text from command line", verbosity=1)
        elif input_file:
            # Determine file type from extension
            file_ext = os.path.splitext(input_file)[1].lower()
            
            try:
                if file_ext == '.csv':
                    df = pd.read_csv(input_file)
                    
                    # Look for text column with standard names
                    for col in ['text', 'content', 'cleaned_text', 'full_text']:
                        if col in df.columns:
                            texts = df[col].tolist()
                            self.log(f"Using column '{col}' from CSV file", verbosity=1)
                            break
                    
                    if not texts:
                        # If no standard column found, use the first text-like column
                        for col in df.columns:
                            if df[col].dtype == 'object':
                                texts = df[col].tolist()
                                self.log(f"Using column '{col}' from CSV file", verbosity=1)
                                break
                    
                    texts_source = 'csv_file'
                    
                elif file_ext == '.json':
                    with open(input_file, 'r') as f:
                        data = json.load(f)
                    
                    # Handle different JSON formats
                    if isinstance(data, list):
                        if all(isinstance(item, str) for item in data):
                            texts = data
                        elif all(isinstance(item, dict) for item in data):
                            # Try to find text field in dictionaries
                            for field in ['text', 'content', 'cleaned_text', 'full_text']:
                                if field in data[0]:
                                    texts = [item.get(field) for item in data if item.get(field)]
                                    self.log(f"Using field '{field}' from JSON objects", verbosity=1)
                                    break
                    elif isinstance(data, dict) and 'texts' in data:
                        texts = data['texts']
                    
                    texts_source = 'json_file'
                    
                else:
                    # Assume plain text file, one text per line
                    with open(input_file, 'r') as f:
                        texts = [line.strip() for line in f if line.strip()]
                    
                    texts_source = 'text_file'
                
                self.log(f"Loaded {len(texts)} texts from {input_file}", verbosity=1)
            
            except Exception as e:
                raise CommandError(f"Error reading input file: {str(e)}")
        
        # Make predictions
        if not texts:
            raise CommandError("No texts found for prediction")
        
        try:
            # Clean the texts before prediction (similar to training)
            cleaned_texts = []
            for text in texts:
                try:
                    cleaned_text = text_cleaning_string(
                        text, 
                        remove_stopwords=True,
                        remove_punctuation=True,
                        remove_digits=False,
                        stemming=False,
                        lemmatization=True
                    )
                except Exception:
                    # Fallback to simpler cleaning
                    cleaned_text = cleanText(text)
                
                if cleaned_text and len(cleaned_text.split()) >= 10:
                    cleaned_texts.append(cleaned_text)
                else:
                    self.log(f"Warning: Text after cleaning is too short and will be skipped: '{text[:50]}...'", level=logging.WARNING)
            
            if not cleaned_texts:
                raise CommandError("No valid texts after cleaning")
            
            # Make predictions
            predictions = self.predict(model_type, model, cleaned_texts)
            
            # Combine the original texts with predictions
            results = []
            for i, text in enumerate(texts):
                if i < len(cleaned_texts):  # Only include texts that were not filtered out
                    results.append({
                        'text': text[:100] + ('...' if len(text) > 100 else ''),
                        'prediction': predictions['predictions'][i],
                        'probability': predictions['probabilities'][i]
                    })
            
            # Output the results
            if output_file:
                # Determine output format based on extension
                out_ext = os.path.splitext(output_file)[1].lower()
                
                if out_ext == '.json':
                    with open(output_file, 'w') as f:
                        output_data = {
                            'metadata': {
                                'team': team_slug,
                                'subject': subject_slug,
                                'model_type': model_type,
                                'model_version': model_version,
                                'timestamp': time.strftime("%Y-%m-%d %H:%M:%S"),
                                'texts_source': texts_source,
                                'num_texts': len(results)
                            },
                            'predictions': results
                        }
                        json.dump(output_data, f, indent=2)
                else:
                    # Default to CSV for other extensions
                    df = pd.DataFrame(results)
                    df.to_csv(output_file, index=False)
                
                self.log(f"Predictions saved to {output_file}", verbosity=1)
            else:
                # Print to stdout
                self.stdout.write("\nPrediction Results:")
                for result in results:
                    self.stdout.write("-" * 80)
                    self.stdout.write(f"Text: {result['text']}")
                    self.stdout.write(f"Prediction: {'Relevant' if result['prediction'] == 1 else 'Not Relevant'}")
                    self.stdout.write(f"Probability: {result['probability']:.4f}")
                self.stdout.write("-" * 80)
                self.stdout.write(f"Total: {len(results)} predictions\n")
        
        except Exception as e:
            raise CommandError(f"Error during prediction: {str(e)}")
        
        self.log("Prediction completed successfully", verbosity=1)
