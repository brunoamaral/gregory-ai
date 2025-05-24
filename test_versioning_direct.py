"""
Direct test script for versioning module.
"""
import os
import sys
import tempfile
import shutil

# Add the django directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from gregory.utils.versioning import make_version_path

def main():
    print("Testing make_version_path function...")
    
    # Create a temporary directory for testing
    temp_dir = tempfile.mkdtemp()
    print(f"Using temporary directory: {temp_dir}")
    
    try:
        # Test creating a version path
        path1 = make_version_path(temp_dir, 'test_team', 'test_subject', 'pubmed_bert')
        print(f"Created first version path: {path1}")
        print(f"Directory exists: {path1.exists()}")
        
        # Test creating a second version path (should add _2 suffix)
        path2 = make_version_path(temp_dir, 'test_team', 'test_subject', 'pubmed_bert')
        print(f"Created second version path: {path2}")
        print(f"Directory exists: {path2.exists()}")
        
        # Test with a different algorithm
        path3 = make_version_path(temp_dir, 'test_team', 'test_subject', 'lstm')
        print(f"Created path with different algorithm: {path3}")
        print(f"Directory exists: {path3.exists()}")
        
        print("\nFunction executed successfully!")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        # Clean up
        shutil.rmtree(temp_dir)
        print(f"Cleaned up temporary directory: {temp_dir}")

if __name__ == "__main__":
    main()
