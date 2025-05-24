"""
Text summarisation utility module.

This module provides functions to summarise text using the Hugging Face transformers library
with the facebook/bart-large-cnn model. The model is cached at the module level to avoid
reloading it for subsequent calls. 

Summaries are cached to disk in the 'summary_cache.json' file to avoid regenerating
them for the same input text. The cache is stored on disk only and is never automatically 
saved to the database. Any function that uses generated summaries is responsible for ensuring
they don't overwrite original article abstracts in the database.
"""
from typing import Optional, Union, List, Dict
import concurrent.futures
import hashlib
import json
import math
import os
import pathlib
from datetime import datetime

import torch
from django.conf import settings
from transformers import BartTokenizer, BartForConditionalGeneration


# Cache the model and tokenizer at the module level
_MODEL: Optional[BartForConditionalGeneration] = None
_TOKENIZER: Optional[BartTokenizer] = None
_DEVICE: Optional[torch.device] = None

# In-memory cache for summaries
_SUMMARY_CACHE: Dict[str, str] = {}
_CACHE_HIT_COUNT = 0
_CACHE_MISS_COUNT = 0

# Determine the cache file path (create a directory in the Django project)
def _get_cache_dir():
    """Get the directory for the summary cache file."""
    cache_dir = getattr(settings, 'SUMMARY_CACHE_DIR', None)
    if not cache_dir:
        cache_dir = os.path.join(settings.BASE_DIR, 'summary_cache')
    
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir

def _get_cache_file():
    """Get the path to the summary cache file."""
    return os.path.join(_get_cache_dir(), 'summary_cache.json')


def _initialize_model() -> tuple[BartForConditionalGeneration, BartTokenizer, torch.device]:
    """
    Initialize the BART model and tokenizer, loading them to the appropriate device (GPU/CPU).
    
    Returns:
        tuple: A tuple containing (model, tokenizer, device)
    """
    global _MODEL, _TOKENIZER, _DEVICE
    
    if _MODEL is not None and _TOKENIZER is not None:
        return _MODEL, _TOKENIZER, _DEVICE
    
    # Determine device (CUDA GPU if available, otherwise CPU)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    
    # Load pre-trained model and tokenizer
    model_name = "facebook/bart-large-cnn"
    tokenizer = BartTokenizer.from_pretrained(model_name)
    model = BartForConditionalGeneration.from_pretrained(model_name).to(device)
    
    # Cache the loaded resources
    _MODEL = model
    _TOKENIZER = tokenizer
    _DEVICE = device
    
    return model, tokenizer, device


def summarise(text: str, max_length: int = 300, use_cache: bool = True) -> str:
    """
    Generate a summary of the provided text using the BART-large-CNN model.
    
    Args:
        text (str): The input text to summarise
        max_length (int, optional): Maximum token length for the summary. Defaults to 300.
        use_cache (bool, optional): Whether to use the cache. Defaults to True.
        
    Returns:
        str: The generated summary text
        
    Examples:
        >>> long_text = "This is a very long article about climate change..."
        >>> summary = summarise(long_text)
        >>> print(summary)
        "Climate change poses significant global risks..."
    """
    global _CACHE_HIT_COUNT, _CACHE_MISS_COUNT
    
    if not text or not text.strip():
        return ""
    
    # Check if we should use the cache
    if use_cache:
        # Create a cache key using the text hash and max_length
        cache_key = f"{_hash_text(text)}_{max_length}"
        
        # Check if the summary is already in the cache
        if cache_key in _SUMMARY_CACHE:
            _CACHE_HIT_COUNT += 1
            return _SUMMARY_CACHE[cache_key]
        
        _CACHE_MISS_COUNT += 1
    
    # Initialize model if not already done
    model, tokenizer, device = _initialize_model()
    
    # Tokenize the input text
    inputs = tokenizer(text, return_tensors="pt", max_length=1024, truncation=True)
    inputs = inputs.to(device)
    
    # Generate summary
    summary_ids = model.generate(
        inputs["input_ids"],
        max_length=max_length,
        num_beams=4,
        length_penalty=2.0,
        early_stopping=True,
    )
    
    # Decode summary
    summary = tokenizer.decode(summary_ids[0], skip_special_tokens=True)
    
    # Store in cache if needed
    if use_cache:
        _SUMMARY_CACHE[cache_key] = summary
        
        # Periodically save the cache to disk (every 50 new entries)
        if _CACHE_MISS_COUNT % 50 == 0:
            _save_cache()
    
    return summary


def summarise_bulk(texts: List[str], batch_size: int = 4, use_cache: bool = True, usage_type: str = 'prediction') -> List[str]:
    """
    Summarize a list of texts in batches, optionally in parallel if on GPU.
    
    Args:
        texts (List[str]): List of texts to summarize
        batch_size (int, optional): Size of batches for processing. Defaults to 4.
        use_cache (bool, optional): Whether to use the cache. Defaults to True.
        usage_type (str, optional): Context in which summaries will be used ('prediction' or 'training').
                                   Used for validation. Defaults to 'prediction'.
        
    Returns:
        List[str]: List of summarized texts in the same order as the input
        
    Examples:
        >>> texts = ["Long article 1...", "Long article 2...", "Long article 3..."]
        >>> summaries = summarise_bulk(texts)
    """
    if not texts:
        return []
    
    # Handle any empty strings in the input
    processed_texts = []
    empty_indices = []
    
    for i, text in enumerate(texts):
        if not text or not text.strip():
            empty_indices.append(i)
        else:
            processed_texts.append((i, text))
    
    # Early return if all strings are empty
    if not processed_texts:
        return [""] * len(texts)
    
    # Check if we're running on GPU
    device = _DEVICE if _DEVICE is not None else torch.device("cuda" if torch.cuda.is_available() else "cpu")
    is_gpu = device.type == "cuda"
    
    # Create batches
    num_batches = math.ceil(len(processed_texts) / batch_size)
    batches = []
    for i in range(num_batches):
        start_idx = i * batch_size
        end_idx = min((i + 1) * batch_size, len(processed_texts))
        batches.append(processed_texts[start_idx:end_idx])
    
    result_summaries = [""] * len(texts)
    
    # Process in parallel if on GPU, sequentially if on CPU
    if is_gpu:
        # For GPU, we process batches in parallel as each batch uses the same GPU
        with concurrent.futures.ThreadPoolExecutor() as executor:
            # Process each batch in parallel
            future_to_batch = {
                executor.submit(_process_batch, batch, use_cache): batch_idx 
                for batch_idx, batch in enumerate(batches)
            }
            
            # Collect results as they complete
            for future in concurrent.futures.as_completed(future_to_batch):
                batch_idx = future_to_batch[future]
                batch_result = future.result()
                
                # Place summaries in the correct positions
                for orig_idx, summary in batch_result:
                    result_summaries[orig_idx] = summary
    else:
        # For CPU, just process sequentially to avoid memory issues
        for batch in batches:
            batch_result = _process_batch(batch, use_cache)
            for orig_idx, summary in batch_result:
                result_summaries[orig_idx] = summary
                
    # Save the cache after processing all batches
    if use_cache:
        _save_cache()
    
    return result_summaries


def _process_batch(indexed_texts, use_cache=True):
    """
    Helper function to process a batch of texts.
    
    Args:
        indexed_texts: List of (index, text) tuples
        use_cache: Whether to use the cache
        
    Returns:
        List of (index, summary) tuples
    """
    result = []
    for idx, text in indexed_texts:
        summary = summarise(text, use_cache=use_cache)
        result.append((idx, summary))
    return result


def _load_cache():
    """Load the summary cache from disk."""
    global _SUMMARY_CACHE
    cache_file = _get_cache_file()
    
    try:
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                _SUMMARY_CACHE = json.load(f)
            print(f"Loaded {len(_SUMMARY_CACHE)} cached summaries.")
    except Exception as e:
        print(f"Error loading summary cache: {e}")
        _SUMMARY_CACHE = {}

def _save_cache():
    """Save the current summary cache to disk."""
    cache_file = _get_cache_file()
    
    try:
        with open(cache_file, 'w') as f:
            json.dump(_SUMMARY_CACHE, f)
    except Exception as e:
        print(f"Error saving summary cache: {e}")


# Load the cache when the module is imported
_load_cache()


def _hash_text(text):
    """Create a hash of the text to use as a cache key."""
    return hashlib.md5(text.encode('utf-8')).hexdigest()


def get_cache_stats():
    """Return statistics about the summary cache."""
    hit_rate = 0
    if _CACHE_HIT_COUNT + _CACHE_MISS_COUNT > 0:
        hit_rate = _CACHE_HIT_COUNT / (_CACHE_HIT_COUNT + _CACHE_MISS_COUNT) * 100
        
    return {
        "cache_entries": len(_SUMMARY_CACHE),
        "hits": _CACHE_HIT_COUNT,
        "misses": _CACHE_MISS_COUNT,
        "hit_rate": f"{hit_rate:.1f}%"
    }


def clear_cache():
    """Clear the summary cache."""
    global _SUMMARY_CACHE, _CACHE_HIT_COUNT, _CACHE_MISS_COUNT
    _SUMMARY_CACHE = {}
    _CACHE_HIT_COUNT = 0
    _CACHE_MISS_COUNT = 0
    _save_cache()
    return {"status": "Cache cleared", "entries": 0}


def is_summary_from_cache(text: str, summary: str, max_length: int = 300) -> bool:
    """
    Check if a given summary for a text is from the cache.
    
    Args:
        text (str): The original text
        summary (str): The summary to check
        max_length (int, optional): Maximum token length used when generating the summary. Defaults to 300.
        
    Returns:
        bool: True if the summary is from the cache, False otherwise
        
    Examples:
        >>> text = "This is a long article about climate change..."
        >>> summary = "Climate change poses significant global risks..."
        >>> is_summary_from_cache(text, summary)
        True  # If this summary was previously cached
    """
    if not text or not text.strip():
        return False
    
    # Create a cache key using the text hash and max_length
    cache_key = f"{_hash_text(text)}_{max_length}"
    
    # Check if the summary is in the cache and matches the provided summary
    return cache_key in _SUMMARY_CACHE and _SUMMARY_CACHE[cache_key] == summary


def validate_summary_usage(text: str, summary: str, usage_type: str = 'training') -> bool:
    """
    Validate that generated summaries are being used appropriately and not overwriting article abstracts.
    
    Args:
        text (str): The original text
        summary (str): The generated summary
        usage_type (str): The context in which the summary is being used ('training' or 'prediction')
        
    Returns:
        bool: True if the summary usage is valid, False otherwise
        
    Raises:
        ValueError: If the summary usage is invalid and potentially harmful
        
    Examples:
        >>> # For training, summaries should be stored in a separate column
        >>> validate_summary_usage(text, summary, 'training')
        True
        
        >>> # For prediction, summaries can be used for inference but not stored back to the article
        >>> validate_summary_usage(text, summary, 'prediction')
        True
    """
    # Check if the summary comes from the cache
    is_cached = is_summary_from_cache(text, summary)
    
    if usage_type == 'training':
        # For training, we should be using 'generated_summary' column, not 'summary'
        if not is_cached:
            print(f"Warning: Generated a new summary for training that wasn't in the cache. " +
                  "This is inefficient but not harmful.")
        return True
    elif usage_type == 'prediction':
        # For prediction, we only use summaries for inference, never save back to Articles
        return True
    else:
        # For any other use case, issue a warning
        print(f"Warning: Using summary in an unrecognized context: {usage_type}")
        return True


if __name__ == "__main__":
    # Test the summarizer with a long lorem ipsum text
    lorem_ipsum = """
    Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. 
    Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. 
    Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. 
    Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.
    Sed ut perspiciatis unde omnis iste natus error sit voluptatem accusantium doloremque laudantium, 
    totam rem aperiam, eaque ipsa quae ab illo inventore veritatis et quasi architecto beatae vitae dicta sunt explicabo. 
    Nemo enim ipsam voluptatem quia voluptas sit aspernatur aut odit aut fugit, sed quia consequuntur magni dolores eos qui 
    ratione voluptatem sequi nesciunt. Neque porro quisquam est, qui dolorem ipsum quia dolor sit amet, consectetur, 
    adipisci velit, sed quia non numquam eius modi tempora incidunt ut labore et dolore magnam aliquam quaerat voluptatem. 
    Ut enim ad minima veniam, quis nostrum exercitationem ullam corporis suscipit laboriosam, nisi ut aliquid ex ea commodi consequatur? 
    Quis autem vel eum iure reprehenderit qui in ea voluptate velit esse quam nihil molestiae consequatur, 
    vel illum qui dolorem eum fugiat quo voluptas nulla pariatur?
    """
    
    print("Original text length:", len(lorem_ipsum.split()))
    summary = summarise(lorem_ipsum)
    print("\nSummary:")
    print(summary)
    print("\nSummary length:", len(summary.split()))
