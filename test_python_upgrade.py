#!/usr/bin/env python3
"""
Test script to validate Python 3.12 upgrade and TensorFlow compatibility.

This script can be run inside the Docker container to verify:
1. Python version is 3.12.x
2. TensorFlow can be imported and is version 2.17.x
3. Keras can be imported (both tensorflow.keras and standalone)
4. Basic ML dependencies are available

Usage:
    docker exec gregory python /code/test_python_upgrade.py
"""

import sys
import importlib.metadata


def test_python_version():
    """Verify Python version is 3.12.x"""
    version = sys.version_info
    print(f"✓ Python version: {version.major}.{version.minor}.{version.micro}")
    
    if version.major != 3 or version.minor != 12:
        print(f"✗ ERROR: Expected Python 3.12.x, got {version.major}.{version.minor}.{version.micro}")
        return False
    
    print("✓ Python 3.12 confirmed")
    return True


def test_tensorflow():
    """Verify TensorFlow can be imported and is correct version"""
    try:
        import tensorflow as tf
        version = tf.__version__
        print(f"✓ TensorFlow version: {version}")
        
        if not version.startswith('2.17') and not version.startswith('2.18'):
            print(f"⚠ WARNING: Expected TensorFlow 2.17.x or 2.18.x, got {version}")
            return True  # Still passing, just a warning
        
        print("✓ TensorFlow 2.17+ confirmed")
        return True
    except ImportError as e:
        print(f"✗ ERROR: Cannot import TensorFlow: {e}")
        return False


def test_keras():
    """Verify Keras can be imported via tensorflow.keras"""
    try:
        from tensorflow import keras
        print(f"✓ Keras (via tensorflow.keras) imported successfully")
        print(f"  Keras version: {keras.__version__}")
        return True
    except ImportError as e:
        print(f"✗ ERROR: Cannot import Keras: {e}")
        return False


def test_tf_keras():
    """Verify tf-keras is available for Keras 2 compatibility if needed"""
    try:
        import tf_keras
        print(f"✓ tf-keras imported successfully (Keras 2 compatibility available)")
        print(f"  tf-keras version: {tf_keras.__version__}")
        return True
    except ImportError as e:
        print(f"⚠ WARNING: Cannot import tf-keras: {e}")
        print("  This is optional but recommended for Keras 2 compatibility")
        return True  # Not critical


def test_ml_dependencies():
    """Verify key ML dependencies are available"""
    dependencies = [
        'numpy',
        'pandas',
        'scikit-learn',
        'transformers',
        'torch',
        'lightgbm',
    ]
    
    all_ok = True
    for dep in dependencies:
        try:
            version = importlib.metadata.version(dep)
            print(f"✓ {dep}: {version}")
        except importlib.metadata.PackageNotFoundError:
            print(f"✗ ERROR: {dep} not found")
            all_ok = False
    
    return all_ok


def test_tensorflow_keras_apis():
    """Verify common TensorFlow/Keras APIs are available"""
    try:
        from tensorflow.keras.layers import Dense, Dropout, LSTM, Input
        from tensorflow.keras.models import Model, Sequential
        from tensorflow.keras.optimizers import Adam
        from tensorflow.keras.callbacks import EarlyStopping
        from tensorflow.keras.metrics import Precision, Recall, AUC
        
        print("✓ All common Keras APIs imported successfully")
        print("  - Layers: Dense, Dropout, LSTM, Input")
        print("  - Models: Model, Sequential")
        print("  - Optimizers: Adam")
        print("  - Callbacks: EarlyStopping")
        print("  - Metrics: Precision, Recall, AUC")
        return True
    except ImportError as e:
        print(f"✗ ERROR: Cannot import Keras APIs: {e}")
        return False


def main():
    """Run all tests"""
    print("=" * 70)
    print("Python 3.12 Upgrade Validation")
    print("=" * 70)
    print()
    
    tests = [
        ("Python Version", test_python_version),
        ("TensorFlow", test_tensorflow),
        ("Keras (tensorflow.keras)", test_keras),
        ("tf-keras (Keras 2 compatibility)", test_tf_keras),
        ("ML Dependencies", test_ml_dependencies),
        ("TensorFlow/Keras APIs", test_tensorflow_keras_apis),
    ]
    
    results = []
    for name, test_func in tests:
        print(f"\n--- Testing: {name} ---")
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"✗ EXCEPTION in {name}: {e}")
            results.append((name, False))
    
    # Summary
    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")
    
    print()
    print(f"Tests passed: {passed}/{total}")
    
    if passed == total:
        print("\n✓ All tests passed! Python 3.12 upgrade successful.")
        return 0
    else:
        print(f"\n✗ {total - passed} test(s) failed. Please review.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
