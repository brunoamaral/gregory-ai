"""
Text summarisation utility module.

This module provides functions to summarise text using the Hugging Face transformers library
with the facebook/bart-large-cnn model. The model is cached at the module level to avoid
reloading it for subsequent calls.
"""
from typing import Optional, Union, List
import concurrent.futures
import math

import torch
from transformers import BartTokenizer, BartForConditionalGeneration


# Cache the model and tokenizer at the module level
_MODEL: Optional[BartForConditionalGeneration] = None
_TOKENIZER: Optional[BartTokenizer] = None
_DEVICE: Optional[torch.device] = None


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


def summarise(text: str, max_length: int = 300) -> str:
    """
    Generate a summary of the provided text using the BART-large-CNN model.
    
    Args:
        text (str): The input text to summarise
        max_length (int, optional): Maximum token length for the summary. Defaults to 300.
        
    Returns:
        str: The generated summary text
        
    Examples:
        >>> long_text = "This is a very long article about climate change..."
        >>> summary = summarise(long_text)
        >>> print(summary)
        "Climate change poses significant global risks..."
    """
    if not text or not text.strip():
        return ""
    
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
    
    return summary


def summarise_bulk(texts: List[str], batch_size: int = 4) -> List[str]:
    """
    Summarize a list of texts in batches, optionally in parallel if on GPU.
    
    Args:
        texts (List[str]): List of texts to summarize
        batch_size (int, optional): Size of batches for processing. Defaults to 4.
        
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
                executor.submit(_process_batch, batch): batch_idx 
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
            batch_result = _process_batch(batch)
            for orig_idx, summary in batch_result:
                result_summaries[orig_idx] = summary
    
    return result_summaries


def _process_batch(indexed_texts):
    """
    Helper function to process a batch of texts.
    
    Args:
        indexed_texts: List of (index, text) tuples
        
    Returns:
        List of (index, summary) tuples
    """
    result = []
    for idx, text in indexed_texts:
        summary = summarise(text)
        result.append((idx, summary))
    return result


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
