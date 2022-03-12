import os.path
import requests
import json
import pandas as pd
from utils.text_utils import cleanText
from utils.text_utils import cleanHTML
import html


# The path to the the local JSON file
SOURCE_DATA_LOCAL = "/python-ml/data/source.json"

# The URL for retrieving the JSON file
SOURCE_DATA_URL = "https://api.gregory-ms.com/articles/all"

# Check if the source data file exists locally (no need to get it from server if already available locally)
# if(not os.path.isfile(SOURCE_DATA_LOCAL)):
    # If not available locally retrieve it and save it locally
r = requests.get(SOURCE_DATA_URL)
with open(SOURCE_DATA_LOCAL, 'w') as outfile:
    json.dump(r.json(), outfile)

# Read the JSON data into a pandas dataframe
dataset = pd.read_json(SOURCE_DATA_LOCAL)

# Give some info on the dataset
dataset.info()

# List of columns that we actually need from the dataset
valid_columns = ["title", "summary", "relevant"]

# Strip the dataset to only those columns
dataset = dataset[valid_columns]

# Clean the title column with the cleanText utility
dataset["title"] = dataset["title"].apply(cleanText)

# Some columns in the summary column seem to be encoded in HTML, let's decode it
dataset["summary"] = dataset["summary"].apply(html.unescape)

# Now let's remove all those HTML tags
dataset["summary"] = dataset["summary"].apply(cleanHTML)

# Now let's clean the resulting text in the summary column
dataset["summary"] = dataset["summary"].apply(cleanText)

# Let's join the two text columns into a single 'terms' column
dataset["terms"] = dataset["title"].astype(str) + " " + dataset["summary"].astype(str)

# We no longer need the two text columns, let's remove them
dataset = dataset[["terms", "relevant"]]

# There are several records in the "relevant" column as NaN. Let's convert them to zeros
dataset["relevant"] = dataset["relevant"].fillna(value = 0)

SOURCE_DATA_CSV = "/python-ml/data/source.csv"

dataset.to_csv(SOURCE_DATA_CSV, index=False)
