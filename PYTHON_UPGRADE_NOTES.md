# Python 3.12 Upgrade Notes

## Summary

This document describes the upgrade from Python 3.11 to Python 3.12 in the Gregory AI Docker images.

## Changes Made

### Docker Images
- **Dockerfile**: Updated from `python:3.11` to `python:3.12`
- **Dockerfile-slim**: Updated from `python:3.11-slim` to `python:3.12-slim`

### Dependencies Updated

#### TensorFlow & Keras
- **tensorflow**: `2.15.0` → `2.17.1`
- **tensorboard**: `2.15.2` → `2.17.1`
- **keras**: `2.15.0` → `3.6.0` (Major version change - Keras 3)
- **tf-keras**: `2.15.0` → `2.17.0` (provides Keras 2 compatibility if needed)
- **tensorflow-estimator**: Removed (deprecated in TensorFlow 2.16+)

## Why Python 3.12?

- Python 3.11.14 was the current version in use
- Python 3.12.12 is the latest stable Python version with full TensorFlow support
- Python 3.13 exists but is not yet supported by TensorFlow (as of December 2024)
- Python 3.14 does not exist yet (expected October 2025)

## TensorFlow Version Constraints

### Compatibility Matrix
- **Python 3.11**: TensorFlow 2.15.0 ✅ (previous setup)
- **Python 3.12**: TensorFlow 2.15.0 ❌ (no prebuilt wheels available)
- **Python 3.12**: TensorFlow 2.16+ ✅ (required for Python 3.12)
- **Python 3.13**: Not supported by any current TensorFlow version

### Why TensorFlow 2.17.1?
- First version to support Python 3.12: TensorFlow 2.16.0
- Chosen version: TensorFlow 2.17.1 (stable, tested, includes bug fixes)
- Alternative: TensorFlow 2.18.0 (newer but removed TensorRT support)

## Keras 3 Migration

TensorFlow 2.16+ switched from Keras 2 to Keras 3 as the default backend.

### Key Changes in Keras 3
1. **API compatibility**: Most common APIs remain compatible
2. **Backend flexibility**: Keras 3 can use TensorFlow, JAX, or PyTorch as backend
3. **Performance improvements**: Better multi-framework support
4. **Model weights**: `model.weights` returns `keras.Variable` instead of `tf.Variable`

### Our Code Impact
The Gregory AI codebase uses:
- `from tensorflow.keras import ...` (standard imports)
- Common Keras layers: Dense, Dropout, LSTM, Input
- Common optimizers: Adam
- Common callbacks: EarlyStopping
- Metrics: Precision, Recall, AUC

These APIs are compatible with Keras 3, so minimal code changes should be needed.

### Using Keras 2 (If Needed)
If compatibility issues arise, you can use Keras 2 via `tf-keras`:

1. Set environment variable before importing TensorFlow:
   ```python
   import os
   os.environ['TF_USE_LEGACY_KERAS'] = '1'
   import tensorflow as tf
   ```

2. Or use `tf_keras` directly:
   ```python
   import tf_keras as keras
   ```

## Testing Recommendations

### 1. Build Test
```bash
cd django
docker build -f Dockerfile -t gregory-test:python312 .
```

### 2. Installation Test
Verify all dependencies install correctly with Python 3.12

### 3. ML Model Tests
```bash
docker exec gregory python manage.py test gregory.tests.test_ml_models
```

### 4. Training Test
Test model training with a small dataset:
```bash
docker exec gregory python manage.py train_models --team <team> --subject <subject> --algo pubmed_bert --epochs 1
```

### 5. Prediction Test
```bash
docker exec gregory python manage.py predict_articles --all-teams
```

## Rollback Plan

If critical issues are discovered:

1. Revert Dockerfiles to `python:3.11` and `python:3.11-slim`
2. Revert requirements.txt changes:
   - `tensorflow==2.15.0`
   - `tensorboard==2.15.2`
   - `keras==2.15.0`
   - `tf-keras==2.15.0`
   - Add back `tensorflow-estimator==2.15.0`

## References

- [TensorFlow 2.17 Release Notes](https://blog.tensorflow.org/2024/07/whats-new-in-tensorflow-217.html)
- [Keras 3 Migration Guide](https://keras.io/guides/migrating_to_keras_3/)
- [TensorFlow Python Version Support](https://www.tensorflow.org/install/pip)
- [Python 3.12 Release Notes](https://docs.python.org/3.12/whatsnew/3.12.html)

## Next Steps

1. ✅ Update Dockerfiles to Python 3.12
2. ✅ Update requirements.txt with compatible TensorFlow/Keras versions
3. ⏳ Test Docker image build
4. ⏳ Run Django tests
5. ⏳ Test ML model training and prediction
6. ⏳ Update CI/CD pipelines if needed
7. ⏳ Deploy to staging environment
8. ⏳ Monitor for any runtime issues
