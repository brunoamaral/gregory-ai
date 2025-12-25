# Training Machine Learning Models

This guide covers how to train the ML models used by Gregory AI to predict article relevance.

## Overview

Gregory AI uses three ML algorithms for predicting article relevance:
- **pubmed_bert**: PubMed BERT fine-tuned model (highest accuracy, slowest)
- **lgbm_tfidf**: LightGBM with TF-IDF features (fast, good accuracy)
- **lstm**: LSTM neural network (moderate speed and accuracy)

## Prerequisites

### Hardware Requirements

| Algorithm | Min RAM | Recommended RAM | GPU Support |
|-----------|---------|-----------------|-------------|
| pubmed_bert | 8GB | 16GB+ | Optional (much faster) |
| lgbm_tfidf | 4GB | 8GB | Not used |
| lstm | 4GB | 8GB | Optional |

### Software Requirements

Ensure all ML dependencies are installed:
```bash
uv pip install tensorflow transformers lightgbm torch
```

## Training on Local Machine

### 1. Set Up Environment

First, ensure you have the database running and environment variables set:

```bash
# Copy example environment file if needed
cp example.env .env

# Edit .env with your database credentials
# Required: POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, SECRET_KEY, FERNET_SECRET_KEY

# Start the database
docker compose up -d db
```

### 2. Activate Python Environment

#### Using uv (Recommended)

```bash
cd django

# Create virtual environment with uv
uv venv

# Activate the environment
source .venv/bin/activate

# Install dependencies
uv pip install -r requirements.txt

# For Apple Silicon GPU support (M1/M2/M3), install compatible versions:
uv pip install 'tensorflow==2.18.0' 'tensorflow-metal==1.2.0'
```

#### Using pip/venv

```bash
cd django

# Create virtual environment (if not exists)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Run Training

#### Basic Training (Single Team/Subject)

```bash
# Train all algorithms for a specific team and subject
python manage.py train_models --team ms-research --subject ms

# Train only BERT for a specific subject
python manage.py train_models --team ms-research --subject ms --algo pubmed_bert

# Train LGBM and LSTM (faster, for testing)
python manage.py train_models --team ms-research --subject ms --algo lgbm_tfidf,lstm
```

#### Training All Teams

```bash
# Train all algorithms for all teams with auto_predict enabled
python manage.py train_models --all-teams
```

#### Advanced Options

```bash
# Use all labeled articles (not just last 90 days)
python manage.py train_models --team ms-research --subject ms --all-articles

# Custom lookback window (e.g., last 180 days)
python manage.py train_models --team ms-research --subject ms --lookback-days 180

# Enable pseudo-labeling (semi-supervised learning)
python manage.py train_models --team ms-research --subject ms --pseudo-label

# Custom probability threshold (default: 0.8)
python manage.py train_models --team ms-research --subject ms --prob-threshold 0.75

# Verbose output for debugging
python manage.py train_models --team ms-research --subject ms --verbose 3

# Debug mode (check ML imports)
python manage.py train_models --team ms-research --subject ms --debug

# Force CPU-only training (if GPU causes bus errors)
python manage.py train_models --team ms-research --subject ms --cpu
```

## Training Inside Docker Container

### 1. Start the Container

```bash
docker compose up -d
```

### 2. Run Training Command

```bash
# Basic training
docker exec gregory python manage.py train_models --team ms-research --subject ms

# With debug output
docker exec gregory python manage.py train_models --team ms-research --subject ms --debug --verbose 3

# Train only fast algorithms (recommended for container)
docker exec gregory python manage.py train_models --team ms-research --subject ms --algo lgbm_tfidf
```

### 3. Monitor Progress

```bash
# Follow container logs
docker logs -f gregory

# Check memory usage
docker stats gregory
```

### Important Notes for Container Training

1. **Memory Limits**: The container has a 12GB memory limit. BERT training may require adjusting this in `docker-compose.yaml`.

2. **Long-Running Jobs**: For BERT training, consider using `nohup` or `screen`:
   ```bash
   docker exec -d gregory python manage.py train_models --team ms-research --subject ms
   ```

3. **Model Persistence**: Models are saved to `./django/models/` which is mounted as a volume, so they persist after container restart.

## Model Output

Trained models are saved to:
```
django/models/{team_slug}/{subject_slug}/{algorithm}/{version}/
```

Each version directory contains:
- Model weights (`.h5` for BERT/LSTM, `.joblib` for LGBM)
- `metrics.json` - Training and validation metrics
- Vectorizer files (for LGBM/LSTM)

## Training Data Requirements

- **Minimum**: At least 2 labeled articles per class (relevant/not relevant)
- **Recommended**: 50+ labeled articles per class for reliable results
- **Ideal**: 200+ labeled articles per class

Check your data before training:
```bash
# In Django shell
docker exec -it gregory python manage.py shell

>>> from gregory.models import ArticleSubjectRelevance, Subject
>>> subject = Subject.objects.get(subject_slug='ms')
>>> ArticleSubjectRelevance.objects.filter(subject=subject, is_relevant=True).count()
>>> ArticleSubjectRelevance.objects.filter(subject=subject, is_relevant=False).count()
```

## Troubleshooting

### Import Errors

Run with `--debug` flag to check ML imports:
```bash
python manage.py train_models --team ms-research --subject ms --debug
```

### Bus Error on Apple Silicon (M1/M2/M3)

If you see a `bus error` when training BERT with TensorFlow Metal:

```
[1] 36717 bus error  python manage.py train_models --all-teams
```

This is a known issue with tensorflow-metal and BERT models. Solutions:

1. **Use CPU-only mode for BERT** (recommended):
   ```bash
   python manage.py train_models --team ms-research --subject ms --algo pubmed_bert --cpu
   ```

2. **Train only LGBM** (doesn't use TensorFlow, fast and reliable):
   ```bash
   python manage.py train_models --team ms-research --subject ms --algo lgbm_tfidf
   ```

3. **Use LGBM + LSTM with GPU, BERT with CPU**:
   ```bash
   # GPU works fine for LGBM and LSTM
   python manage.py train_models --team ms-research --subject ms --algo lgbm_tfidf,lstm
   
   # Use CPU for BERT
   python manage.py train_models --team ms-research --subject ms --algo pubmed_bert --cpu
   ```

4. **Check TensorFlow versions** - ensure you have compatible versions:
   ```bash
   uv pip install 'tensorflow==2.18.0' 'tensorflow-metal==1.2.0'
   ```

**Note**: BERT training on CPU is slower but stable. On an M2 Mac, expect ~2-3 hours for BERT training vs ~30 minutes with a working GPU.

### Out of Memory

1. Reduce batch size (edit `train_models.py`)
2. Use LGBM instead of BERT
3. Increase container memory limit in `docker-compose.yaml`

### Slow Training

1. BERT is naturally slow on CPU (hours vs minutes on GPU)
2. Use `--algo lgbm_tfidf` for faster training
3. Reduce dataset with `--lookback-days 30`

### No Labeled Data

Ensure articles have been manually reviewed:
1. Go to Django admin → Article Subject Relevances
2. Set `is_relevant` to True/False for articles
3. Re-run training

## GPU Support (Optional)

### macOS with Apple Silicon (M1/M2/M3)

**Important**: Docker on macOS cannot access Apple's GPU. Docker runs in a Linux VM which doesn't support Metal.

For GPU-accelerated training on Apple Silicon, you must run **natively on macOS** (not in Docker):

#### Using uv (Recommended)

**Important**: tensorflow-metal 1.2.0 requires TensorFlow 2.18.x. Newer TensorFlow versions (2.19+, 2.20+) are NOT compatible.

```bash
cd django

# Create and activate virtual environment
uv venv
source .venv/bin/activate

# Install TensorFlow 2.18.0 with Metal support (compatible versions)
uv pip install 'tensorflow==2.18.0' 'tensorflow-metal==1.2.0'

# Install other dependencies
uv pip install -r requirements.txt

# Verify GPU is available
python -c "import tensorflow as tf; print('GPUs:', tf.config.list_physical_devices('GPU'))"

# You should see: GPUs: [PhysicalDevice(name='/physical_device:GPU:0', device_type='GPU')]

# Run training natively with GPU acceleration
python manage.py train_models --team ms-research --subject ms --verbose 3
```

#### Using pip/venv

**Important**: tensorflow-metal 1.2.0 requires TensorFlow 2.18.x. Newer TensorFlow versions are NOT compatible.

```bash
cd django

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install TensorFlow 2.18.0 with Metal support first
pip install 'tensorflow==2.18.0' 'tensorflow-metal==1.2.0'

# Install other dependencies
pip install -r requirements.txt

# Verify GPU is available
python -c "import tensorflow as tf; print('GPUs:', tf.config.list_physical_devices('GPU'))"

# Run training natively
python manage.py train_models --team ms-research --subject ms --verbose 3
```

**Note**: PyTorch also supports MPS on Apple Silicon:
```python
import torch
print(torch.backends.mps.is_available())  # Should print True
```

### Linux with NVIDIA GPU

For NVIDIA GPU-accelerated BERT training on Linux:

1. Install NVIDIA Container Toolkit
2. Update `docker-compose.yaml`:
   ```yaml
   gregory:
     deploy:
       resources:
         reservations:
           devices:
             - driver: nvidia
               count: 1
               capabilities: [gpu]
   ```
3. Ensure `tensorflow-gpu` is installed in the container

### Cloud GPU (Alternative)

For faster training without local GPU setup, consider:
- **Google Colab** (free GPU tier available)
- **AWS EC2** with GPU instances (p3.2xlarge or g4dn.xlarge)
- **Google Cloud** with GPU VMs

## Scheduling Training

For automated retraining, add to crontab:
```bash
# Retrain models weekly on Sunday at 2 AM
0 2 * * 0 docker exec gregory python manage.py train_models --all-teams --algo lgbm_tfidf >> /var/log/gregory-training.log 2>&1
```

## See Also

- [04-machine-learning.md](04-machine-learning.md) - ML architecture details
- [spec for ML Training.md](spec%20for%20ML%20Training.md) - Technical specification
