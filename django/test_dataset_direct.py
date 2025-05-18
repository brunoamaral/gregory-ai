"""
Simple script to test dataset functions.
"""
import os
import django
import pandas as pd

# Set up Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "admin.settings")
django.setup()

# Import dataset functions
from gregory.utils.dataset import collect_articles, build_dataset, train_val_test_split

def main():
    """Test the dataset functions directly."""
    print("Testing dataset functions...")
    
    # Test with synthetic data for train_val_test_split
    print("\nTesting train_val_test_split:")
    
    # Create a synthetic dataset
    data = []
    for i in range(100):
        data.append({
            'article_id': i,
            'text': f'This is article {i}',
            'relevant': i % 2  # 50 relevant, 50 not relevant
        })
    df = pd.DataFrame(data)
    
    print(f"Original dataset size: {len(df)}")
    print(f"Class distribution: {df['relevant'].value_counts().to_dict()}")
    
    # Split the dataset
    try:
        train_df, val_df, test_df = train_val_test_split(df)
        print(f"Train dataset size: {len(train_df)} ({len(train_df)/len(df)*100:.1f}%)")
        print(f"Validation dataset size: {len(val_df)} ({len(val_df)/len(df)*100:.1f}%)")
        print(f"Test dataset size: {len(test_df)} ({len(test_df)/len(df)*100:.1f}%)")
        
        print("\nClass distributions:")
        print(f"Train: {train_df['relevant'].value_counts(normalize=True).to_dict()}")
        print(f"Validation: {val_df['relevant'].value_counts(normalize=True).to_dict()}")
        print(f"Test: {test_df['relevant'].value_counts(normalize=True).to_dict()}")
        
        print("\nFunction executed successfully!")
    except Exception as e:
        print(f"Error: {e}")
    
    # Test stratification failure case
    print("\nTesting stratification failure case:")
    single_class_df = pd.DataFrame([
        {'article_id': i, 'text': f'Article {i}', 'relevant': 1}
        for i in range(10)
    ])
    
    try:
        train_val_test_split(single_class_df)
    except ValueError as e:
        print(f"Expected error raised: {e}")
    
    print("\nTests completed!")

if __name__ == "__main__":
    main()
