# Gregory AI Machine Learning Module

This directory contains the machine learning components for the Gregory AI Django project.

## Installation

The ML components require additional dependencies beyond the base Django project. Install them using:

```bash
cd /home/brunoamaral/gregory-ai
uv add -r requirements-ml.txt
```

## Components

- **BertTrainer**: BERT-based model training for biomedical text
- **LGBMTfidfTrainer**: LightGBM with TF-IDF features
- **LSTMTrainer**: LSTM neural network for text classification
- **Pseudo-labeling**: Tools for semi-supervised learning

## Troubleshooting

### Import Errors

If you're experiencing crashes when importing ML modules, try these steps:

1. **Verify dependencies**: Make sure all required packages are installed:
   ```bash
   uv pip list | grep -E "numpy|pandas|scikit-learn|lightgbm|tensorflow|transformers"
   ```

2. **Run the debug script**: Identify which specific module is causing issues:
   ```bash
   uv run python debug_imports.py
   ```

3. **Check for GPU conflicts**: If you're using TensorFlow with GPU:
   ```bash
   uv run python -c "import tensorflow as tf; print(tf.config.list_physical_devices('GPU'))"
   ```

4. **Memory issues**: Some ML components require significant memory:
   ```bash
   uv run python -c "import os; print(f'Available memory: {os.popen('free -h').read()}')"
   ```

5. **Update packages**: Try updating problematic packages:
   ```bash
   uv add numpy pandas scikit-learn --upgrade
   ```

### Training Command

When running the training command, you can use the `--verbose=3` flag to get more diagnostic information.

For example:
```bash
uv run python django/manage.py train_models --team=example --subject=test --verbose=3
```