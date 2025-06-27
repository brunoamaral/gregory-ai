# Machine Learning in Gregory

Gregory uses machine learning algorithms to automatically identify relevant research articles based on their title, abstract, and other features.

## Model Architecture

Gregory implements multiple machine learning algorithms that work together:

1. **PubMed BERT** - A fine-tuned BERT model pre-trained on biomedical text
2. **LGBM with TF-IDF** - LightGBM model with TF-IDF vectorization of text
3. **LSTM** - Long Short-Term Memory network for sequence analysis

## Training Process

The models are trained using a combination of:
- Manually labeled articles (expert-reviewed)
- Pseudo-labeled articles (high-confidence predictions)
- Transfer learning from pre-trained biomedical models

## Training the Models

Gregory provides a Django management command for training models:

```bash
python manage.py train_models --team TEAM_SLUG --subject SUBJECT_SLUG
```

### Command Options

| Option | Description |
|--------|-------------|
| `--team TEAM_SLUG` | Team slug to train models for |
| `--all-teams` | Train models for all teams |
| `--subject SUBJECT_SLUG` | Subject slug within the chosen team |
| `--all-articles` | Use all labeled articles (ignores 90-day window) |
| `--lookback-days DAYS` | Override the default 90-day window |
| `--algo ALGORITHMS` | Comma-separated list of algorithms |
| `--prob-threshold THRESHOLD` | Probability threshold (default: 0.8) |
| `--version VERSION` | Manual version tag |
| `--pseudo-label` | Run BERT self-training loop |
| `--verbose LEVEL` | Verbosity level (0-3) |

## Model Versioning

Models are versioned using a YYYYMMDD format with an optional _n suffix for multiple versions on the same day. This allows tracking of model performance over time.

## Evaluation Metrics

Gregory evaluates models using:
- Precision
- Recall
- F1 Score
- ROC AUC
- Confusion Matrix

## Model Serving

Trained models are stored in the database and served through the Django application. Predictions are made in real-time as new articles are discovered.

## Feature Importance

The LGBM model provides feature importance analysis, helping to understand which terms and features are most predictive of relevance.

## Monitoring and Improvement

Model performance is continuously monitored. When performance drops below a threshold, retraining is triggered automatically.

For more details, see:
- [Model Training Tutorial](training-tutorial.md)
- [Advanced Model Configuration](advanced-configuration.md)
- [Troubleshooting ML Issues](troubleshooting.md)
