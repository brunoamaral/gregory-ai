import os
import django

# Set up Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "admin.settings")
django.setup()

# Now import our function
from gregory.utils.summariser import summarise, summarise_bulk

def run_tests():
    print("Testing summarise function...")
    test_text = "This is a test text that needs to be summarized."
    summary = summarise(test_text)
    print(f"Input: {test_text}")
    print(f"Output: {summary}")
    
    print("\nTesting summarise_bulk function...")
    texts = ["Test text 1", "Test text 2", ""]
    summaries = summarise_bulk(texts, batch_size=2)
    print(f"Input: {texts}")
    print(f"Output: {summaries}")
    
    print("\nTests completed!")

if __name__ == "__main__":
    run_tests()
