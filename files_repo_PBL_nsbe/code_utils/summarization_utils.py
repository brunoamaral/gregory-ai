import pandas as pd
from transformers import T5Tokenizer, TFT5ForConditionalGeneration
from tqdm import tqdm
import tensorflow as tf

def text_summarization(df, text_column='text_processed', batch_size=8, max_length=128, min_length=64):
    """
    Summarize a batch of texts from a dataframe using the T5 model.

    Args:
        df (pd.DataFrame): DataFrame containing the text data.
        text_column (str): Name of the column containing the text to summarize.
        batch_size (int): Number of texts to process in each batch.
        max_length (int): Maximum length of the summarized text.
        min_length (int): Minimum length of the summarized text.

    Returns:
        pd.DataFrame: DataFrame with an additional column 'summary' containing the summarized texts.
    """
    # Initialize the tokenizer and model for T5
    t5_tokenizer = T5Tokenizer.from_pretrained('t5-small')
    t5_model = TFT5ForConditionalGeneration.from_pretrained('t5-small')

    def summarize_batch(texts, max_length=max_length):
        inputs = t5_tokenizer(texts, return_tensors="tf", padding=True, truncation=True, max_length=512)
        summary_ids = t5_model.generate(
            inputs['input_ids'], 
            attention_mask=inputs['attention_mask'], 
            max_length=max_length, 
            min_length=min_length, 
            length_penalty=2.0, 
            num_beams=4, 
            early_stopping=True
        )
        summaries = [t5_tokenizer.decode(g, skip_special_tokens=True, clean_up_tokenization_spaces=True) for g in summary_ids]
        return summaries

    def apply_summarization(df, batch_size=batch_size):
        summaries = []
        for i in tqdm(range(0, len(df), batch_size), desc="Summarizing"):
            batch_texts = df[text_column].iloc[i:i+batch_size].tolist()
            summaries.extend(summarize_batch(batch_texts))
        return summaries

    # Apply summarization to the data
    df['summary'] = apply_summarization(df)
    
    return df
