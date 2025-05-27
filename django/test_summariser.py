
from gregory.utils.summariser import summarise_bulk

texts = ["Test text 1", "Test text 2", ""]
print("Testing summarise_bulk function...")
print(f"Input: {texts}")
print(f"Output: {summarise_bulk(texts, batch_size=2)}")
print("Test completed!")

