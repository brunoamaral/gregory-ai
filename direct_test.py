"""
Direct test for summariser functions.
"""
import os
import sys

# Add the django directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from gregory.utils.summariser import summarise, summarise_bulk

def main():
    print("Testing summarise function...")
    test_text = "This is a test text that needs to be summarized."
    print(f"Input: {test_text}")
    
    try:
        summary = summarise(test_text)
        print(f"Output: {summary}")
        print("Function executed successfully!")
    except Exception as e:
        print(f"Error executing summarise: {e}")
    
    print("\nTesting summarise_bulk function...")
    texts = ["First test text", "Second test text", ""]
    print(f"Input: {texts}")
    
    try:
        summaries = summarise_bulk(texts, batch_size=2)
        print(f"Output: {summaries}")
        print("Function executed successfully!")
    except Exception as e:
        print(f"Error executing summarise_bulk: {e}")

if __name__ == "__main__":
    main()
