#!/usr/bin/env python
"""
Debug script to identify import issues with gregory.ml module
"""
import sys
import traceback

def test_import(module_name, from_module=None):
    try:
        if from_module:
            exec(f"from {from_module} import {module_name}")
            print(f"✓ Successfully imported {module_name} from {from_module}")
        else:
            exec(f"import {module_name}")
            print(f"✓ Successfully imported {module_name}")
        return True
    except Exception as e:
        print(f"✗ Failed to import {module_name}: {str(e)}")
        print("\nTraceback:")
        traceback.print_exc()
        return False

if __name__ == "__main__":
    # Set up Django environment
    import os
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gregory.settings")
    
    # Basic imports
    test_import("django")
    test_import("numpy")
    test_import("pandas")
    test_import("sklearn")
    
    # Gregory app imports
    test_import("gregory")
    test_import("gregory.models")
    test_import("gregory.utils")
    
    # ML module imports - individual components
    test_import("gregory.ml")
    test_import("BertTrainer", "gregory.ml.bert_wrapper")
    test_import("LGBMTfidfTrainer", "gregory.ml.lgbm_wrapper")
    test_import("LSTMTrainer", "gregory.ml.lstm_wrapper")
    
    # The problematic import
    test_import("get_trainer", "gregory.ml")
    
    print("\nDebugging complete.")