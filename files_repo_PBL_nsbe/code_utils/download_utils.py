"""
This module contains utility functions used for downloading articles from GregoryAI.
"""

import pandas as pd
import requests
import concurrent.futures
import math
import os
import shutil
from datetime import datetime
import zipfile
from io import BytesIO

def get_initial_count(url, api_key=None):

    """
    Fetch the initial page to get the total count of articles. Auxiliary function to fetch_all_articles.

    Parameters:
        url (str): The API endpoint to fetch the articles count.
        api_key (str, optional): The API key for authorization, if required.

    Returns:
        int: The total count of articles if successful, None otherwise.
    """
    headers = {'Authorization': api_key} if api_key else {}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Ensures HTTPError is raised for bad requests
        data = response.json()
        return data['count']
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch initial count: {e}")
        return None

def fetch_articles_page(url, page, api_key=None):

    """
    Fetch a single page of articles. Auxiliary function to fetch_all_articles.

    Parameters:
        url (str): The API endpoint to fetch articles.
        page (int): The page number to fetch.
        api_key (str, optional): The API key for authorization, if required.

    Returns:
        list: A list of articles from the requested page, or an empty list if the request fails.
    """
    params = {'page': page}
    headers = {'Authorization': api_key} if api_key else {}
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()['results']
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch page {page}: {e}")
        return []

def get_articles(url, api_key=None):
    
    """
    Fetch all articles data from the given URL. Auxiliary function to fetch_all_articles.

    Parameters:
        url (str): The API endpoint to fetch all articles.
        api_key (str, optional): The API key for authorization, if required.

    Returns:
        dict: The JSON response from the API if successful, None otherwise.
    """
    headers = {'Authorization': api_key} if api_key else {}
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Error: Received response code {response.status_code}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return None
    
def save_dataframe_to_date_folder(df, filename):

    """
    Save a DataFrame to a CSV file within a date-specific folder inside a base 'data' folder.
    Auxiliary function to fetch_all_articles.

    This function performs the following steps:
    1. Checks if a 'data' folder exists in the current directory. If it doesn't, creates it.
    2. Checks if a folder with the current date (format: YYYY-MM-DD) exists inside the 'data' folder. 
       If it doesn't, creates it. If it does, deletes its contents.
    3. Saves the DataFrame to a CSV file within the date-specific folder.

    Parameters:
        df (pd.DataFrame): The DataFrame to be saved.
        filename (str): The name of the CSV file (including .csv extension).

    Returns:
        None
    """
    current_dir = os.getcwd()
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(base_dir)
    
    # Define the folder paths
    base_folder = 'data'
    current_date_folder = datetime.now().strftime('%Y-%m-%d')
    date_folder_path = os.path.join(base_folder, current_date_folder)
    file_path = os.path.join(date_folder_path, filename)

    # Create the 'data' folder if it does not exist
    if not os.path.exists(base_folder):
        os.makedirs(base_folder)

    # Create the date folder if it does not exist
    if not os.path.exists(date_folder_path):
        os.makedirs(date_folder_path)

    # Save the DataFrame to the CSV file
    df.to_csv(file_path)
    os.chdir(current_dir)
    print(f"DataFrame saved to {file_path}")

def fetch_all_articles(url, old_articles, n_articles_inference, api_key=None, articles_per_page=10):

    """
    Fetch all articles using concurrent futures and save the data to a CSV file.
    Returns 

    Parameters:
        url (str): The API endpoint to fetch articles.
                   Use this url 'https://api.gregory-ms.com/articles/?format=json'
        old_articles (string or pd.DataFrame: The previous articles data. In case there is not previous data, pass an empty DataFrame.
        n_articles_inference (int or string): The number of articles return seperately for inference.
                                              If this number is smaller than the total number of new articles, then all new_articles will be returned.
                                              The string 'max' can be passed to return all new articles for inference.
        api_key (str, optional): The API key for authorization, if required.
        articles_per_page (int, optional): The number of articles per page (default is 10).

    Returns:
        train_df (pd.DataFrame): A DataFrame containing all the articles, except for the last n_articles_inference.
        inference_df (pd.DataFrame): A DataFrame containing a number of newly fetched articles for inference, specified by n_articles_inference.
    """

    try:
        old_articles_df = pd.read_csv(old_articles, index_col=0)
    except:
        if not isinstance(old_articles, pd.DataFrame):
            raise ValueError("The previous articles data is not a valid DataFrame or path.")
        else:
            old_articles_df = old_articles

    total_articles = get_initial_count(url, api_key)
    if not total_articles:
        print("Failed to get the total count of articles.")
        return

    total_pages = math.ceil(total_articles / articles_per_page)
    print(f"Total articles: {total_articles}, Total pages: {total_pages}")

    articles = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(fetch_articles_page, url, page, api_key) for page in range(1, total_pages + 1)]
        for future in concurrent.futures.as_completed(futures):
            articles.extend(future.result())

    articles_df = pd.DataFrame(articles)
    articles_df = articles_df.set_index('article_id', drop=True)

    new_articles_id = set(articles_df.index) - set(old_articles_df.index)
    all_new_articles = articles_df.loc[list(new_articles_id)]

    if isinstance(n_articles_inference, str) and n_articles_inference == 'max':
        inference_df = all_new_articles
    elif isinstance(n_articles_inference, int):
        if n_articles_inference >= len(all_new_articles):
            inference_df = all_new_articles
        else:
            try:
                inference_df = all_new_articles.tail(n_articles_inference)
            except:
                raise ValueError("The number of articles for inference is not a valid integer or 'max'.")

    remaining_articles_id = set(articles_df.index) - set(inference_df.index)
    train_df = articles_df.loc[list(remaining_articles_id)]

    save_dataframe_to_date_folder(train_df, 'train_articles.csv')
    save_dataframe_to_date_folder(inference_df, 'inference_articles.csv')

    return train_df, inference_df

def download_and_extract_zip(url, old_articles, n_articles_inference):

    """
    Fetch all articles using concurrent futures and save the data to a CSV file.
    Returns 

    Parameters:
        url (str): The url with the articles.zip
                   Use this url 'https://gregory-ms.com/developers/articles.zip'
        old_articles (string or pd.DataFrame: The previous articles data. In case there is no previous data, pass an empty DataFrame.
        n_articles_inference (int or string): The number of articles return seperately for inference.
                                              If this number is smaller than the total number of new articles, then all new_articles will be returned.
                                              The string 'max' can be passed to return all new articles for inference.

    Returns:
        train_df (pd.DataFrame): A DataFrame containing all the articles, except for the last n_articles_inference.
        inference_df (pd.DataFrame): A DataFrame containing a number of newly fetched articles for inference, specified by n_articles_inference.
    """

    try:
        old_articles_df = pd.read_csv(old_articles)
    except:
        if not isinstance(old_articles, pd.DataFrame):
            raise ValueError("The previous articles data is not a valid DataFrame or path.")
        else:
            old_articles_df = old_articles

    response = requests.get('https://gregory-ms.com/developers/articles.zip')
    response.raise_for_status()

    with zipfile.ZipFile(BytesIO(response.content)) as z:   # BytesIO is used to convert the content to a file-like object, avoiding the need to extract the contents to disk
        for file_info in z.infolist():
            if file_info.filename.endswith('.csv'):
                with z.open(file_info) as file:
                    articles_df = pd.read_csv(file).drop(columns='Unnamed: 0')

    new_articles = articles_df.loc[~articles_df["article_id"].isin(old_articles_df["article_id"])]

    if isinstance(n_articles_inference, str) and n_articles_inference == 'max':
        inference_df = new_articles
    elif isinstance(n_articles_inference, int):
        if n_articles_inference >= len(new_articles):
            inference_df = new_articles
        else:
            try:
                inference_df = new_articles.tail(n_articles_inference)
            except:
                raise ValueError("The number of articles for inference is not a valid integer or 'max'.")

    train_df = articles_df.loc[~articles_df["article_id"].isin(inference_df["article_id"])]

    save_dataframe_to_date_folder(train_df, 'train_articles.csv')
    save_dataframe_to_date_folder(inference_df, 'inference_articles.csv')

    return train_df, inference_df
    